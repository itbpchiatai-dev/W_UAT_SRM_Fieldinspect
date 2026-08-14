"""End-to-end smoke for scripts/scaffold.py.

Runs the scaffold into a temp dir and asserts the output is syntactically
valid + the four pre-commit checks pass on the scaffolded code itself.
This is the single regression net that catches escape-mangled f-strings
in the scaffold template (the failure mode that bit us during Phase 3).

Run: `python -m pytest tests/` (or `pytest tests/test_scaffold_smoke.py -v`)
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from scaffold import (  # noqa: E402
    _scaffold_backend,
    _scaffold_docker,
    _scaffold_frontend,
    _scaffold_global_standards,
    _scaffold_tooling,
)


def _cfg(**overrides) -> SimpleNamespace:
    base = dict(
        project_slug="smoke-test",
        project_display_name="Smoke Test",
        default_language="th",
        auth_scope="both",
        jwt_secret_key="0" * 64,
        mfa_encryption_key="1" * 64,
        bootstrap_admin_email="",
        bootstrap_admin_auth_type="sso",
        bootstrap_initial_password="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def scaffolded(tmp_path: Path) -> Path:
    cfg = _cfg()
    _scaffold_backend(cfg, tmp_path)
    _scaffold_frontend(cfg, tmp_path)
    _scaffold_docker(cfg, tmp_path)
    _scaffold_tooling(cfg, tmp_path)
    _scaffold_global_standards(cfg, tmp_path)
    return tmp_path


def test_every_python_file_parses(scaffolded: Path) -> None:
    """scaffold.py uses raw triple-quoted templates — one bad escape and a
    whole project becomes uninstallable. Parse every emitted .py."""
    failures: list[str] = []
    for path in sorted(scaffolded.rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(scaffolded)}:{exc.lineno}: {exc.msg}")
    assert not failures, "\n".join(failures)


def test_required_files_exist(scaffolded: Path) -> None:
    required = [
        # Backend foundation
        "backend/pyproject.toml",
        "backend/.env",
        "backend/.env.example",
        "backend/Dockerfile",
        "backend/app/main.py",
        "backend/app/core/config.py",
        "backend/app/core/pii.py",
        "backend/app/core/logging.py",
        "backend/app/core/scheduler.py",
        "backend/app/db/base.py",
        "backend/app/db/session.py",
        "backend/app/db/seed.py",
        # Logging models + services
        "backend/app/db/models/activity_log.py",
        "backend/app/db/models/system_log.py",
        "backend/app/db/models/ai_call_log.py",
        "backend/app/services/loggers/activity_logger.py",
        "backend/app/services/loggers/system_logger.py",
        "backend/app/services/loggers/ai_call_logger.py",
        "backend/app/services/loggers/partition_manager.py",
        "backend/app/services/loggers/retention.py",
        "backend/app/api/decorators.py",
        "backend/app/api/v1/installed_routers.py",
        "backend/app/integrations/claude_ai.py",
        "backend/app/integrations/registry.py",
        # Schemas
        "backend/app/schemas/base.py",
        # Migrations (3 partitioned log tables + 3 auth tables)
        "backend/alembic/versions/2026_01_01_0000-0001_activity_logs.py",
        "backend/alembic/versions/2026_01_01_0100-0002_system_logs.py",
        "backend/alembic/versions/2026_01_01_0200-0003_ai_call_logs.py",
        "backend/alembic/versions/2026_01_02_0000-0004_auth_core.py",
        "backend/alembic/versions/2026_01_02_0100-0005_menus_permissions.py",
        "backend/alembic/versions/2026_01_02_0200-0006_app_settings.py",
        # Auth models (Sprint 3)
        "backend/app/db/models/user.py",
        "backend/app/db/models/role.py",
        "backend/app/db/models/permission.py",
        "backend/app/db/models/role_permission.py",
        "backend/app/db/models/user_role.py",
        "backend/app/db/models/user_permission_override.py",
        "backend/app/db/models/menu_item.py",
        "backend/app/db/models/app_setting.py",
        # Auth services
        "backend/app/auth/__init__.py",
        "backend/app/auth/jwt_service.py",
        "backend/app/auth/password.py",
        "backend/app/auth/azure_ad.py",
        "backend/app/auth/dependencies.py",
        "backend/app/auth/permissions.py",
        # Auth routers
        "backend/app/api/v1/auth.py",
        "backend/app/api/v1/me.py",
        "backend/app/api/v1/users.py",
        "backend/app/api/v1/roles.py",
        "backend/app/api/v1/permissions.py",
        "backend/app/api/v1/menus.py",
        "backend/app/api/v1/admin_settings.py",
        # Auth schemas + seed
        "backend/app/schemas/auth.py",
        "backend/app/seed.py",
        # Pattern C admin-config reader
        "backend/app/services/app_setting_service.py",
        # Frontend
        "frontend/package.json",
        "frontend/src/index.css",
        "frontend/src/App.tsx",
        "frontend/src/routes.tsx",
        "frontend/tailwind.config.ts",
        # Phase B — auth absorption (frontend foundation)
        "frontend/src/types/auth.ts",
        "frontend/src/api/auth.ts",
        "frontend/src/api/me.ts",
        "frontend/src/api/adminSettings.ts",
        "frontend/src/stores/auth.ts",
        "frontend/src/hooks/useAuth.ts",
        "frontend/src/hooks/useHasPermission.ts",
        "frontend/src/components/RequireAuth.tsx",
        "frontend/src/components/Layout/AppLayout.tsx",
        "frontend/src/components/Layout/TopBar.tsx",
        "frontend/src/components/Layout/Sidebar.tsx",
        "frontend/src/components/Layout/UserMenu.tsx",
        "frontend/src/pages/Login.tsx",
        "frontend/src/pages/Dashboard.tsx",
        "frontend/src/pages/SettingsIndex.tsx",
        # Phase C — admin pages + API clients + RequirePermission
        "frontend/src/components/RequirePermission.tsx",
        "frontend/src/api/users.ts",
        "frontend/src/api/roles.ts",
        "frontend/src/api/permissions.ts",
        "frontend/src/api/menus.ts",
        "frontend/src/pages/settings/Users.tsx",
        "frontend/src/pages/settings/Roles.tsx",
        "frontend/src/pages/settings/Permissions.tsx",
        "frontend/src/pages/settings/Menus.tsx",
        "frontend/src/pages/settings/AuthSettings.tsx",
        # Docker
        "docker/docker-compose.yml",
        "docker/init-db.sql",
        "docker-compose.yml",
        # Tooling
        ".pre-commit-config.yaml",
        "scripts/checks/no_real_secrets_in_examples.py",
        "scripts/checks/no_direct_ai_sdk.py",
        "scripts/checks/camel_base_model_audit.py",
        "scripts/checks/no_dict_in_endpoint.py",
        ".github/workflows/ci.yml",
        ".github/workflows/security.yml",
        # Prod-parity smoke
        "docker-compose.smoke.yml",
        "scripts/smoke-prod.sh",
        "scripts/smoke-prod.bat",
        # VS Code workspace
        ".vscode/settings.json",
        ".vscode/extensions.json",
        # CT global
        "frontend/src/components/AupModal.tsx",
    ]
    missing = [p for p in required if not (scaffolded / p).exists()]
    assert not missing, f"scaffold missed files: {missing}"


def test_no_undefined_tailwind_tokens(scaffolded: Path) -> None:
    """Every `hsl(var(--X))` in tailwind.config.ts must be defined in index.css."""
    import re
    css = (scaffolded / "frontend/src/index.css").read_text(encoding="utf-8")
    tw = (scaffolded / "frontend/tailwind.config.ts").read_text(encoding="utf-8")
    css_vars = set(re.findall(r"--([\w-]+):", css))
    tw_refs = set(re.findall(r"hsl\(var\(--([\w-]+)\)\)", tw))
    missing = tw_refs - css_vars
    assert not missing, f"tailwind references undefined CSS vars: {missing}"


def test_frontend_typography_scale_is_readable(scaffolded: Path) -> None:
    """Keep Thai body/UI text at the agreed readable sizes and line heights."""
    css = (scaffolded / "frontend/src/index.css").read_text(encoding="utf-8")
    tw = (scaffolded / "frontend/tailwind.config.ts").read_text(encoding="utf-8")
    dashboard = (scaffolded / "frontend/src/pages/Dashboard.tsx").read_text(
        encoding="utf-8"
    )

    assert "font-size: 1rem;" in css
    assert "line-height: 1.65;" in css
    assert "xs: ['0.8125rem', { lineHeight: '1.6' }]" in tw
    assert "sm: ['0.9375rem', { lineHeight: '1.6' }]" in tw
    assert "base: ['1rem', { lineHeight: '1.65' }]" in tw
    assert "xl: ['1.3125rem', { lineHeight: '1.45' }]" in tw
    assert "--muted-foreground: 220 10% 38%;" in css
    assert "--accent-readable: 47 61% 31%;" in css
    assert "--success-readable: 122 45% 30%;" in css
    assert "--warning-readable: 28 80% 32%;" in css
    assert "input, select, textarea {" in css
    assert "font-size: 1rem !important;" in css
    assert "min-height: 2.75rem;" in css
    assert '<h1 className="text-xl font-bold">' in dashboard


@pytest.mark.parametrize(
    "check, target_glob, exempt_glob",
    [
        ("camel_base_model_audit.py", "backend/app/schemas/*.py", None),
        ("no_dict_in_endpoint.py", "backend/app/api/**/*.py", None),
        ("no_direct_ai_sdk.py", "backend/app/**/*.py", "*/integrations/*"),
    ],
)
def test_scaffolded_code_passes_check(
    scaffolded: Path, check: str, target_glob: str, exempt_glob: str | None
) -> None:
    """The scaffold MUST emit code that passes its own pre-commit checks."""
    targets: list[Path] = []
    for p in scaffolded.glob(target_glob):
        if exempt_glob and p.match(exempt_glob):
            continue
        targets.append(p)
    if not targets:
        pytest.skip(f"no targets for {check}")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "checks" / check), *map(str, targets)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{check} failed on scaffolded code:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Sprint 1 vanilla-blocker regression guards (PR #3).
#
# Each assertion below pins a specific emission that previously shipped
# broken and was fixed in PR #3. A future refactor or merge conflict could
# silently revert any of these; the tests below make that loud.
# ---------------------------------------------------------------------------


def test_vite_env_dts_emitted_with_client_reference(scaffolded: Path) -> None:
    """Bug 5 — without this file, `tsc` fails on import.meta.env (typecheck
    breaks for every consumer). The triple-slash directive is the load-
    bearing line; the rest of the file augments ImportMetaEnv."""
    vite_env = scaffolded / "frontend/src/vite-env.d.ts"
    assert vite_env.exists(), "frontend/src/vite-env.d.ts must be scaffolded"
    content = vite_env.read_text(encoding="utf-8")
    assert "vite/client" in content, (
        "vite-env.d.ts must contain the `vite/client` reference directive"
    )


def test_backend_depends_on_sync_psycopg(scaffolded: Path) -> None:
    """Bug 16 — alembic needs the sync psycopg driver to run multi-statement
    DDL (asyncpg rejects it). Without this dep, `alembic upgrade head` blows
    up on the very first migration."""
    pyproject = (scaffolded / "backend/pyproject.toml").read_text(encoding="utf-8")
    assert "psycopg[binary]" in pyproject, (
        "backend/pyproject.toml must list psycopg[binary] (sync driver for alembic)"
    )


def test_session_module_defines_both_session_forms(scaffolded: Path) -> None:
    """Bug 21 — the auto-commit get_db dependency is what makes endpoints
    actually persist their writes; the caller-commits get_db_session is what
    the seed/CLI path uses. Losing either silently rolls back real data."""
    session_py = (scaffolded / "backend/app/db/session.py").read_text(encoding="utf-8")
    assert "async def get_db" in session_py, (
        "session.py must define async get_db (FastAPI dependency)"
    )
    assert "await session.commit()" in session_py, (
        "session.py's get_db must auto-commit (await session.commit())"
    )
    assert "async def get_db_session" in session_py, (
        "session.py must define async get_db_session (CLI/scheduler context manager)"
    )


def test_alembic_env_uses_sync_psycopg(scaffolded: Path) -> None:
    """Bug 16 — alembic env.py must use a SYNC engine path (`create_engine`
    or `engine_from_config`, never `async_engine_from_config`) and reference
    the `psycopg` driver, not `asyncpg`. The async variant produced the
    `cannot insert multiple commands into a prepared statement` failure."""
    env_py = (scaffolded / "backend/alembic/env.py").read_text(encoding="utf-8")
    has_sync_engine = "create_engine" in env_py or "engine_from_config" in env_py
    assert has_sync_engine, (
        "alembic/env.py must use a sync engine constructor "
        "(create_engine or engine_from_config), not async_engine_from_config"
    )
    assert "async_engine_from_config" not in env_py, (
        "alembic/env.py must NOT use async_engine_from_config (Bug 16)"
    )
    assert "psycopg" in env_py, "alembic/env.py must reference the sync psycopg driver"
    assert "+psycopg" in env_py, (
        "alembic/env.py must rewrite the URL to the +psycopg driver"
    )


def test_alembic_env_has_offline_mode_guard(scaffolded: Path) -> None:
    """P2 followup — alembic must branch on `context.is_offline_mode()` so
    `alembic upgrade --sql` (dry-run / review pipelines) works without a
    live DB. Losing this guard re-breaks offline migrations."""
    env_py = (scaffolded / "backend/alembic/env.py").read_text(encoding="utf-8")
    assert "is_offline_mode()" in env_py, (
        "alembic/env.py must branch on context.is_offline_mode() "
        "so --sql / offline migrations work"
    )


def test_alembic_env_uses_make_url(scaffolded: Path) -> None:
    """P3 followup — env.py must swap the driver via `make_url(...).set(...)`
    rather than naive str.replace, so URLs that happen to contain
    `+asyncpg` in the password/host can't corrupt parsing."""
    env_py = (scaffolded / "backend/alembic/env.py").read_text(encoding="utf-8")
    assert "make_url" in env_py, (
        "alembic/env.py must use make_url() to swap drivers safely "
        "(naive str.replace breaks if +asyncpg appears in the password)"
    )


def test_user_model_columns_all_have_migrations(scaffolded: Path) -> None:
    """Dogfood 2026-06-01 — the User model carried is_rejected,
    rejection_reason, approval_token_hash and approval_token_expires_at, but
    no migration created them. `alembic upgrade head` then produced a users
    table missing those columns and `python -m app.seed` crashed with
    UndefinedColumnError before the bootstrap super-admin existed — so NO
    freshly-scaffolded project could log in. Guard: every mapped_column on
    the User model must be referenced by some migration."""
    import re

    model = (scaffolded / "backend/app/db/models/user.py").read_text(encoding="utf-8")
    cols = set(re.findall(r"(\w+):\s*Mapped\[.*?\]\s*=\s*mapped_column", model))
    versions = scaffolded / "backend/alembic/versions"
    migrations = "\n".join(
        p.read_text(encoding="utf-8") for p in versions.glob("*.py")
    )
    missing = sorted(c for c in cols if c not in migrations)
    assert not missing, (
        f"User model columns absent from every migration: {missing} — "
        "add a migration so `alembic upgrade head` + seed don't crash"
    )


def test_alembic_env_renders_url_with_real_password(scaffolded: Path) -> None:
    """Regression — `str(URL)` masks the password as ``***``, causing
    alembic to authenticate with the literal string ``***`` and fail.
    The URL MUST be rendered via ``.render_as_string(hide_password=False)``
    (or by passing the URL object straight through, but our template
    materializes a string for set_main_option, so the explicit render is
    the only safe form).
    """
    env_py = (scaffolded / "backend/alembic/env.py").read_text(encoding="utf-8")
    assert "render_as_string(hide_password=False)" in env_py, (
        "alembic/env.py must render the URL with hide_password=False; "
        "str(URL) masks the password as '***' and breaks authentication"
    )
    # And specifically ensure str(make_url(...)) isn't lurking — if a
    # future refactor wraps the render in str() we want it caught here.
    assert "str(make_url" not in env_py, (
        "str(make_url(...)) masks the password — use "
        "make_url(...).render_as_string(hide_password=False) instead"
    )


def test_audited_decorator_does_not_self_commit(scaffolded: Path) -> None:
    """Followup contract — @audited must NOT call `await db.commit()` itself.
    The surrounding get_db dependency owns the commit boundary; a double-
    commit (or commit-inside-decorator) is what caused the auth-seed silent
    rollback in the original report."""
    decorator_files = list((scaffolded / "backend/app/api").rglob("decorators.py"))
    assert decorator_files, "expected backend/app/api/decorators.py to be scaffolded"
    for f in decorator_files:
        text = f.read_text(encoding="utf-8")
        assert "await db.commit()" not in text, (
            f"{f.relative_to(scaffolded)} must not embed `await db.commit()` — "
            "commit is owned by the get_db dependency boundary"
        )


def test_claude_ai_integration_does_not_self_commit(scaffolded: Path) -> None:
    """Followup contract — the claude_ai integration logs via ai_call_logger
    but must NOT commit on its own. Same dependency-boundary contract as
    @audited above."""
    claude_file = scaffolded / "backend/app/integrations/claude_ai.py"
    assert claude_file.exists(), "expected backend/app/integrations/claude_ai.py"
    text = claude_file.read_text(encoding="utf-8")
    assert "await db.commit()" not in text, (
        "claude_ai.py must not embed `await db.commit()` — "
        "commit is owned by the get_db dependency boundary"
    )


def test_scaffold_emits_auth_token_lib(scaffolded: Path) -> None:
    """Bug 19 — scaffold owns lib/auth-token.ts as the SINGLE token store.

    The file must expose the canonical API (getAccessToken / setAccessToken /
    clearAccessToken / authHeaders) and must NOT use localStorage as the
    primary store — sessionStorage is the only browser-side mirror.
    """
    f = scaffolded / "frontend/src/lib/auth-token.ts"
    assert f.exists(), "scaffold must emit frontend/src/lib/auth-token.ts"
    text = f.read_text(encoding="utf-8")
    for sym in ("getAccessToken", "setAccessToken", "clearAccessToken", "authHeaders"):
        assert f"export function {sym}" in text, f"missing export: {sym}"
    # sessionStorage is the canonical browser mirror; localStorage would be
    # the bug we just fixed.
    assert "sessionStorage" in text, "auth-token.ts must mirror to sessionStorage"
    # No CALLS into localStorage — comments mentioning the word are fine.
    for offender in ("localStorage.getItem", "localStorage.setItem", "localStorage.removeItem"):
        assert offender not in text, (
            f"auth-token.ts must NOT call {offender} (Bug 19 — XSS exfil vector)"
        )
    # The canonical storage key the auth module's pages will share.
    assert "auth.accessToken" in text


def test_scaffold_emits_api_client_with_silent_refresh(scaffolded: Path) -> None:
    """Bug 20 — api/client.ts must read its token from lib/auth-token.ts
    (NOT localStorage) and ship a response interceptor that calls
    /api/v1/auth/refresh once on 401 with a `_retry` infinite-loop guard.
    """
    f = scaffolded / "frontend/src/api/client.ts"
    assert f.exists(), "scaffold must emit frontend/src/api/client.ts"
    text = f.read_text(encoding="utf-8")
    # Token pulled from the canonical store — not localStorage
    assert "from '../lib/auth-token'" in text, (
        "api/client.ts must import from ../lib/auth-token (single source of truth)"
    )
    for offender in ("localStorage.getItem", "localStorage.setItem"):
        assert offender not in text, (
            f"api/client.ts must NOT call {offender} anymore (Bug 19)"
        )
    # Silent-refresh interceptor
    assert "/api/v1/auth/refresh" in text, (
        "api/client.ts must wire the silent-refresh endpoint"
    )
    assert "interceptors.response.use" in text, (
        "api/client.ts must register a response interceptor for 401 handling"
    )
    # Infinite-loop guard
    assert "_retry" in text, (
        "api/client.ts must guard against refresh loops via a `_retry` flag"
    )
    # withCredentials for the refresh cookie
    assert "withCredentials" in text


def test_scaffold_emits_auth_bootstrap_component(scaffolded: Path) -> None:
    """Bug 20 + Phase B — AuthBootstrap is the React side of silent refresh.

    Phase B made `enabled` optional with a default of `true`; tests
    + stories can still opt out by passing `enabled={false}`. The
    refreshUrl prop stays optional. Bootstrap must push the refreshed
    token into the canonical store via setAccessToken.
    """
    f = scaffolded / "frontend/src/components/AuthBootstrap.tsx"
    assert f.exists(), "scaffold must emit components/AuthBootstrap.tsx"
    text = f.read_text(encoding="utf-8")
    assert "export interface AuthBootstrapProps" in text
    # `enabled` is optional now (default true) — accept either declaration.
    assert ("enabled?: boolean" in text) or ("enabled: boolean" in text), (
        "AuthBootstrapProps must declare an `enabled` boolean prop"
    )
    assert "refreshUrl?:" in text
    assert "/api/v1/auth/refresh" in text, "default refreshUrl must point at the auth endpoint"
    assert "setAccessToken" in text, "AuthBootstrap must push refreshed token into the store"


def test_scaffold_app_uses_auth_bootstrap(scaffolded: Path) -> None:
    """Phase B — App.tsx mounts <AuthBootstrap/> (no `enabled={false}` opt-out
    anymore; the default-true bootstrap is the new normal). Confirm the
    component is wrapped around the route tree and imported from the
    right module.
    """
    app = (scaffolded / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert "AuthBootstrap" in app, "App.tsx must wrap routes in <AuthBootstrap>"
    # No regression to the Phase A `enabled={false}` opt-out.
    assert "<AuthBootstrap enabled={false}" not in app, (
        "Phase B App.tsx must NOT ship <AuthBootstrap enabled={false}> — "
        "auth is now baked into the scaffold and bootstrap defaults to true"
    )
    assert "from './components/AuthBootstrap'" in app


# ---------------------------------------------------------------------------
# Sprint 2 — Bug 9: ActivityLogger contract for module callers.
#
# auth module's `_audit` shims call:
#   ActivityLogger(db).log(action=..., actor_id=..., target_id=...,
#                          is_security_event=..., risk_level=..., extra=...)
# The emitted ActivityLogger MUST accept that shape — and also keep
# accepting the existing host shape (user=, action_type=, metadata=, ...).
# ---------------------------------------------------------------------------


def _activity_logger_log_signature(scaffolded: Path):
    """Parse the emitted ActivityLogger.log() AST and return its arg names.

    Loading the module would pull in fastapi/sqlalchemy at import time, so
    we walk the AST instead — sufficient for kwarg-name contract checks.
    """
    import ast as _ast

    src = (scaffolded / "backend/app/services/loggers/activity_logger.py").read_text(
        encoding="utf-8"
    )
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef) and node.name == "ActivityLogger":
            for item in node.body:
                if isinstance(item, _ast.AsyncFunctionDef) and item.name == "log":
                    return item.args
    raise AssertionError("ActivityLogger.log not found in emitted file")


def test_scaffold_activity_logger_accepts_user_kwarg(scaffolded: Path) -> None:
    """Host shape — @audited decorator passes ``user=`` (a User object)."""
    args = _activity_logger_log_signature(scaffolded)
    kw_names = {a.arg for a in args.kwonlyargs}
    assert "user" in kw_names, (
        "ActivityLogger.log() must accept `user=` — the @audited decorator "
        "passes a User object so user_id + user_email_masked can be derived"
    )


def test_scaffold_activity_logger_accepts_actor_id_kwarg(scaffolded: Path) -> None:
    """Alternate caller shape — seed scripts and background jobs that only
    hold a UUID (no User instance) must be able to log an audit row.
    """
    args = _activity_logger_log_signature(scaffolded)
    kw_names = {a.arg for a in args.kwonlyargs}
    assert "actor_id" in kw_names, (
        "ActivityLogger.log() must accept `actor_id=` — callers without a "
        "User instance pass a UUID directly"
    )


def test_scaffold_activity_logger_accepts_module_kwargs(scaffolded: Path) -> None:
    """Module shape — full kwarg set used by auth module `_audit` shims."""
    args = _activity_logger_log_signature(scaffolded)
    kw_names = {a.arg for a in args.kwonlyargs}
    required = {"action", "actor_id", "target_id", "is_security_event",
                "risk_level", "extra"}
    missing = required - kw_names
    assert not missing, (
        f"ActivityLogger.log() must accept module kwargs (Bug 9). Missing: {missing}"
    )


def test_scaffold_activity_logger_resolves_user_over_actor_id(scaffolded: Path) -> None:
    """When both `user` and `actor_id` are supplied, `user.id` wins.

    Contract: hosts that wrap module routers can layer their User object
    on top of a module call without the module's actor_id silently
    overriding it.
    """
    src = (scaffolded / "backend/app/services/loggers/activity_logger.py").read_text(
        encoding="utf-8"
    )
    # The resolution block must check `user is not None` before falling back
    # to actor_id. We look for the `if user is not None:` guard followed by
    # an `else` branch that uses actor_id.
    user_branch = src.find("if user is not None")
    assert user_branch != -1, (
        "ActivityLogger.log() must explicitly check `if user is not None` "
        "to give `user=` precedence over `actor_id=`"
    )
    # The fallback to actor_id must come after the user branch.
    fallback = src.find("resolved_user_id = actor_id", user_branch)
    assert fallback != -1, (
        "ActivityLogger.log() must fall back to `actor_id` in the `else` "
        "branch when `user` is None"
    )


def test_scaffold_activity_logger_merges_extra_and_metadata(scaffolded: Path) -> None:
    """`extra` (module shape) and `metadata` (host shape) write to the same
    column. The emitted logger must accept both names and merge them so a
    caller can pass either without losing data.
    """
    src = (scaffolded / "backend/app/services/loggers/activity_logger.py").read_text(
        encoding="utf-8"
    )
    assert "metadata: dict" in src, "log() must still accept `metadata=`"
    assert "extra: dict" in src, "log() must accept `extra=` (module shape)"
    # Both names ultimately feed the same extra_metadata column.
    assert "extra_metadata=" in src


def test_auth_pyproject_has_required_deps(scaffolded: Path) -> None:
    """Sprint 3 — auth subsystem needs bcrypt, pyotp, msal, python-jose.
    python-jose + passlib already shipped; we add bcrypt direct + pyotp.
    """
    pyproject = (scaffolded / "backend/pyproject.toml").read_text(encoding="utf-8")
    for dep in ("bcrypt", "pyotp", "msal", "python-jose"):
        assert dep in pyproject, f"backend/pyproject.toml must list {dep}"


def test_auth_routers_wired_into_installed_routers(scaffolded: Path) -> None:
    """The default installed_routers.py must mount all 7 auth routers."""
    txt = (scaffolded / "backend/app/api/v1/installed_routers.py").read_text(encoding="utf-8")
    for prefix in (
        "/api/v1/auth",
        "/api/v1/me",
        "/api/v1/users",
        "/api/v1/roles",
        "/api/v1/permissions",
        "/api/v1/menus",
        "/api/v1/admin/settings",
    ):
        assert prefix in txt, f"installed_routers.py must mount {prefix}"


def test_auth_seed_is_idempotent_pattern(scaffolded: Path) -> None:
    """Each insert in seed.py must be guarded by an existence check —
    `select(... where ... == ...).scalar_one_or_none()` followed by
    `if existing is None`. We grep for the pattern rather than running
    the seed because it touches the DB.
    """
    seed = (scaffolded / "backend/app/seed.py").read_text(encoding="utf-8")
    assert "scalar_one_or_none" in seed
    assert "if existing is None" in seed
    assert "AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL" in seed
    assert "hash_password" in seed, "local-auth bootstrap must bcrypt the password"
    assert (
        'os.getenv("AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL", "").strip().lower()' in seed
    ), "bootstrap super-admin email must be normalized before lookup/create"
    assert "from sqlalchemy import func, select" in seed
    assert "func.lower(User.email) == email" in seed


def test_bootstrap_admin_email_is_lowercased_in_generated_env(tmp_path: Path) -> None:
    """Bootstrap email is identity data, so generated projects store it in
    one canonical lowercase form from the first seed onward.
    """
    cfg = _cfg(bootstrap_admin_email="Admin.User@Example.COM")
    _scaffold_backend(cfg, tmp_path)

    env = (tmp_path / "backend/.env").read_text(encoding="utf-8")
    assert "AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL=admin.user@example.com" in env


def test_migrations_use_gen_random_uuid_not_uuid_ossp(scaffolded: Path) -> None:
    """The log migrations must not call uuid_generate_v4() — it requires
    the uuid-ossp extension. Scaffold's
    docker/init-db.sql installs it for the local docker container, but
    L2/L3 projects pointing at a centralized/managed Postgres get a fresh
    database without the extension and `alembic upgrade head` blows up
    with ``function uuid_generate_v4() does not exist``.

    gen_random_uuid() is built into Postgres 13+ and needs no extension,
    so it works against any reasonable Postgres target.
    """
    versions = scaffolded / "backend/alembic/versions"
    assert versions.is_dir(), "expected backend/alembic/versions/"
    offenders = []
    for migration in versions.rglob("*.py"):
        content = migration.read_text(encoding="utf-8")
        if "uuid_generate_v4" in content:
            offenders.append(migration.relative_to(scaffolded))
    assert not offenders, (
        f"migrations must not call uuid_generate_v4() — use gen_random_uuid() "
        f"(no extension required, Postgres 13+ built-in). Offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Phase B — frontend auth absorption (login screen, RequireAuth, AppLayout,
# Zustand store, /me hooks). Tests here pin contract surfaces, NOT
# implementation details, so reasonable refactors don't snap them.
# ---------------------------------------------------------------------------


def test_phase_b_frontend_deps_present(scaffolded: Path) -> None:
    """Phase B adds Zustand + react-hook-form + zod + MSAL to package.json."""
    pkg = (scaffolded / "frontend/package.json").read_text(encoding="utf-8")
    for dep in (
        "zustand",
        "react-hook-form",
        "\"zod\"",                 # 'zod' (quoted) to avoid matching the zodResolver hint
        "@hookform/resolvers",
        "@azure/msal-browser",
    ):
        assert dep in pkg, f"package.json must declare {dep} (Phase B)"


def test_phase_b_login_and_layout_wired(scaffolded: Path) -> None:
    """Phase B contract:

    1. App.tsx imports Login + AppLayout + RequireAuth and mounts /login.
    2. The Zustand store exposes the canonical auth slice
       (user/permissionKeys/isAuthenticated + login/logout actions).
    3. Login page reads getPublicAuthSettings + calls login + ssoRedirect.

    Each assertion pins one wire connecting two scaffold artifacts —
    losing any of them re-breaks the boot-into-login-screen flow.
    """
    app = (scaffolded / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert "/login" in app, "App.tsx must mount the /login route"
    assert "<Login" in app and "from './pages/Login'" in app
    assert "RequireAuth" in app and "AppLayout" in app, (
        "App.tsx must wrap protected routes in RequireAuth + AppLayout"
    )

    store = (scaffolded / "frontend/src/stores/auth.ts").read_text(encoding="utf-8")
    assert "useAuthStore" in store, "stores/auth.ts must export useAuthStore"
    for sym in ("user", "permissionKeys", "isAuthenticated", "hydrateFromServer", "login", "logout"):
        assert sym in store, f"auth store must expose `{sym}`"

    login_page = (scaffolded / "frontend/src/pages/Login.tsx").read_text(encoding="utf-8")
    assert "getPublicAuthSettings" in login_page, (
        "Login.tsx must read /admin/settings/public to decide which providers to show"
    )
    assert "ssoRedirect" in login_page, "Login.tsx must wire the SSO redirect button"
    # react-hook-form + zod form validation
    assert "useForm" in login_page and "zodResolver" in login_page
    assert "showPassword" in login_page and "Eye" in login_page and "EyeOff" in login_page
    assert "type={showPassword ? 'text' : 'password'}" in login_page


def test_frontend_env_sets_branded_app_name(scaffolded: Path) -> None:
    """TopBar reads VITE_APP_NAME; the scaffold must emit it from the
    project display name so the header becomes `Chia Tai – <project>`.
    """
    env = (scaffolded / "frontend/.env").read_text(encoding="utf-8")
    env_example = (scaffolded / "frontend/.env.example").read_text(encoding="utf-8")
    topbar = (scaffolded / "frontend/src/components/Layout/TopBar.tsx").read_text(encoding="utf-8")
    dockerfile = (scaffolded / "frontend/Dockerfile").read_text(encoding="utf-8")

    assert "VITE_APP_NAME=Chia Tai – Smoke Test" in env
    assert "VITE_APP_NAME=Chia Tai – <project-display-name>" in env_example
    assert "import.meta.env.VITE_APP_NAME" in topbar
    assert "ARG VITE_APP_NAME" in dockerfile and "ENV VITE_APP_NAME=$VITE_APP_NAME" in dockerfile


def test_local_auth_normalizes_email_at_boundaries(scaffolded: Path) -> None:
    """Local auth should be case-insensitive for email while keeping the
    password itself untouched/case-sensitive.
    """
    auth_api = (scaffolded / "backend/app/api/v1/auth.py").read_text(encoding="utf-8")
    users_api = (scaffolded / "backend/app/api/v1/users.py").read_text(encoding="utf-8")
    frontend_auth = (scaffolded / "frontend/src/api/auth.ts").read_text(encoding="utf-8")
    login_page = (scaffolded / "frontend/src/pages/Login.tsx").read_text(encoding="utf-8")

    assert "from sqlalchemy import func, select" in auth_api
    assert "normalized_email = payload.email.strip().lower()" in auth_api
    assert ".where(func.lower(User.email) == normalized_email)" in auth_api
    assert ".where(func.lower(User.email) == email)" in auth_api
    assert "from sqlalchemy import func, select" in users_api
    assert "normalized_email = payload.email.strip().lower()" in users_api
    assert "func.lower(User.email) == normalized_email" in users_api
    assert "email=normalized_email" in users_api
    assert "email: email.trim().toLowerCase()" in frontend_auth
    assert "values.email.trim().toLowerCase()" in login_page


def test_user_email_normalization_migration_emitted(scaffolded: Path) -> None:
    migration = (
        scaffolded
        / "backend/alembic/versions/2026_01_02_0600-0010_normalize_user_emails.py"
    )
    assert migration.exists(), "migration 0010 must normalize existing mixed-case user emails"
    text = migration.read_text(encoding="utf-8")
    assert 'down_revision = "0009_user_approval_fields"' in text
    assert "GROUP BY lower(email)" in text
    assert "HAVING count(*) > 1" in text
    assert "UPDATE users SET email = lower(trim(email))" in text


# ---------------------------------------------------------------------------
# Phase C — admin pages + API clients + RequirePermission wiring.
# ---------------------------------------------------------------------------


def test_phase_c_settings_pages_use_require_permission(scaffolded: Path) -> None:
    """App.tsx must wrap each /settings/* admin route in RequirePermission
    with the matching menu.* perm key. Losing the guard is a silent
    privilege-escalation regression — admin without role.view could still
    deep-link into /settings/roles.
    """
    app = (scaffolded / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert "RequirePermission" in app, "App.tsx must import RequirePermission"
    expected_pairs = {
        # v3.0.3 — menu visibility merged into the read perm so granting
        # the action right also surfaces the menu (no orphan menus).
        "/settings/users": "users.read",
        "/settings/roles": "roles.read",
        "/settings/menus": "menus.read",
        "/settings/auth": "admin_settings.read",
    }
    for path, perm in expected_pairs.items():
        assert path in app, f"App.tsx must mount {path}"
        assert f'perm="{perm}"' in app, (
            f"App.tsx must gate {path} with RequirePermission perm=\"{perm}\""
        )


def test_phase_c_api_clients_use_canonical_endpoints(scaffolded: Path) -> None:
    """Each Phase C API client must call the v1 endpoints documented in
    the plan (installed_routers.py mounts them at /api/v1/...). A typo in
    the path is a silent 404 — pin it here.
    """
    base = scaffolded / "frontend/src/api"
    expected = {
        "users.ts": "/api/v1/users",
        "roles.ts": "/api/v1/roles",
        "permissions.ts": "/api/v1/permissions",
        "menus.ts": "/api/v1/menus",
    }
    for fname, prefix in expected.items():
        text = (base / fname).read_text(encoding="utf-8")
        assert prefix in text, f"{fname} must call {prefix}"
    # adminSettings.ts gets the listAllSettings + updateSetting helpers
    admin = (base / "adminSettings.ts").read_text(encoding="utf-8")
    for sym in ("listAllSettings", "updateSetting"):
        assert sym in admin, f"adminSettings.ts must export {sym} (Phase C)"


def test_phase_c_settings_index_links_to_admin_pages(scaffolded: Path) -> None:
    """SettingsIndex must be a real-link landing page (not the disabled-
    placeholder grid from Phase B) — each card a <Link to="/settings/*">,
    gated by useHasPermission so admins missing a perm don't see the card.
    """
    idx = (scaffolded / "frontend/src/pages/SettingsIndex.tsx").read_text(encoding="utf-8")
    assert "useHasPermission" in idx, (
        "SettingsIndex.tsx must filter cards by useHasPermission (Phase C)"
    )
    assert "Link" in idx, "SettingsIndex.tsx must use <Link> instead of disabled divs"
    for path in ("/settings/users", "/settings/roles", "/settings/menus", "/settings/auth"):
        assert path in idx, f"SettingsIndex.tsx must link to {path}"


def test_phase_c_auth_settings_honors_scope_ceiling(scaffolded: Path) -> None:
    """AuthSettings page must consult VITE_AUTH_SCOPE so the compile-time
    ceiling is reflected in the toggle state BEFORE the user PUTs (the
    backend also rejects, but a visibly-disabled toggle is the better UX).
    """
    page = (scaffolded / "frontend/src/pages/settings/AuthSettings.tsx").read_text(encoding="utf-8")
    assert "VITE_AUTH_SCOPE" in page, (
        "AuthSettings must read VITE_AUTH_SCOPE to enforce the compile-time ceiling"
    )
    # Both toggles must be present.
    for key in ("auth.local.enabled", "auth.sso.enabled", "auth.local.signup_enabled"):
        assert key in page, f"AuthSettings must reference setting key {key}"


# ---------------------------------------------------------------------------
# Phase D — docs + tests for the auth absorption sprint.
#
# These are smoke-level integration assertions that read emitted file
# content and verify the structural contract documented in
# docs/auth.md §11-§12 + docs/admin-config.md §C.7 + docs/frontend.md §12.
# No HTTP / DB / process work — pure code-shape inspection.
# ---------------------------------------------------------------------------


def test_permission_resolution_includes_overrides(scaffolded: Path) -> None:
    """auth/dependencies.py::_compute_effective_permissions must iterate
    user.permission_overrides AFTER unioning role permissions and respect
    the `granted` boolean (True = extra grant, False = revoke).

    Losing the override loop silently reverts Pattern B to role-only
    permissions — a privilege regression that would be invisible in any
    test that only checks role assignment.
    """
    deps = (scaffolded / "backend/app/auth/dependencies.py").read_text(encoding="utf-8")
    # The role union must happen — perms set seeded from role.permissions.
    assert "for role in user.roles" in deps, (
        "dependencies.py must union permissions across all of user.roles"
    )
    # The override loop must follow.
    assert "for override in user.permission_overrides" in deps, (
        "dependencies.py must iterate user.permission_overrides to apply Pattern B"
    )
    # Both branches of `granted` must be handled.
    assert "override.granted" in deps, (
        "dependencies.py must consult the override.granted field"
    )
    assert ".add(" in deps and ".discard(" in deps, (
        "dependencies.py must `.add()` granted overrides and `.discard()` revoked ones"
    )


def test_provider_toggle_respects_auth_scope_ceiling(scaffolded: Path) -> None:
    """admin_settings.py::update_setting must read AUTH_SCOPE from env and
    reject PUTs that violate the compile-time ceiling. The lock direction
    MUST match the meaning of each scope (and the seed defaults + the SPA's
    VITE_AUTH_SCOPE gating):

        internal_only = Azure AD only -> auth.local.enabled locked false
        external_only = local only    -> auth.sso.enabled   locked false

    Losing this — or inverting it — is a security regression: the runtime UI
    could re-enable the very provider the deployer disabled at scaffold time
    (e.g. switching local auth back on in an Azure-only deployment).
    """
    admin = (scaffolded / "backend/app/api/v1/admin_settings.py").read_text(encoding="utf-8")
    assert "AUTH_SCOPE" in admin, (
        "admin_settings.py must read AUTH_SCOPE env to enforce the ceiling"
    )
    assert "os.getenv(\"AUTH_SCOPE\"" in admin, (
        "admin_settings.py must call os.getenv('AUTH_SCOPE', ...) — env-driven, not hardcoded"
    )
    # The lock map must bind each scope to the CORRECT locked key. We assert
    # on the exact dict-entry substring so an inversion (the bug fixed in
    # v3.0.20) fails loudly instead of passing on a mere key-presence check.
    assert '"internal_only": {"auth.local.enabled": False}' in admin, (
        "internal_only (Azure-only) must lock auth.local.enabled OFF, not sso"
    )
    assert '"external_only": {"auth.sso.enabled": False}' in admin, (
        "external_only (local-only) must lock auth.sso.enabled OFF, not local"
    )
    # And the inverted forms must NOT appear (guards against a silent revert).
    assert '"internal_only": {"auth.sso.enabled": False}' not in admin, (
        "internal_only must NOT lock sso (that inverts the ceiling)"
    )
    assert '"external_only": {"auth.local.enabled": False}' not in admin, (
        "external_only must NOT lock local (that inverts the ceiling)"
    )
    # The PUT handler must raise on locked-key violations.
    assert "locked" in admin and "400" in admin, (
        "admin_settings.py must reject ceiling violations with HTTP 400"
    )


def test_public_settings_endpoint_returns_camel_case(scaffolded: Path) -> None:
    """admin_settings.py /public route must map dotted DB keys to camelCase
    JSON keys the SPA can consume directly
    (PublicAuthSettings interface uses authLocalEnabled / authSsoEnabled).
    A snake/dot leak would force the Login page to re-key the response
    or crash on a runtime type mismatch.
    """
    admin = (scaffolded / "backend/app/api/v1/admin_settings.py").read_text(encoding="utf-8")
    # The /public route must exist.
    assert "/public" in admin, "admin_settings.py must declare a /public route"
    # The camelCase keys returned to the SPA.
    for camel in ("authLocalEnabled", "authSsoEnabled"):
        assert camel in admin, (
            f"admin_settings.py /public must emit {camel} (camelCase for SPA consumption)"
        )
    # And the dotted DB keys must be mapped (the source side of the rename).
    for dotted in ("auth.local.enabled", "auth.sso.enabled"):
        assert dotted in admin, (
            f"admin_settings.py /public must map from {dotted} (dotted DB key)"
        )


def test_login_page_fail_open_on_settings_500(scaffolded: Path) -> None:
    """pages/Login.tsx must wrap getPublicAuthSettings() in try/catch and
    fall back to BOTH providers visible if the call errors — login page
    must never block on a settings outage. The backend re-enforces the
    actual toggle on the submit path, so a guess-wrong here is just a
    momentary UX glitch, not a security hole.
    """
    login = (scaffolded / "frontend/src/pages/Login.tsx").read_text(encoding="utf-8")
    assert "getPublicAuthSettings" in login, (
        "Login.tsx must call getPublicAuthSettings()"
    )
    # The try/catch wrapper.
    assert "try {" in login and "catch" in login, (
        "Login.tsx must wrap getPublicAuthSettings() in try/catch"
    )
    # The fail-open state flag.
    assert "settingsError" in login, (
        "Login.tsx must track a settingsError state to drive the fail-open branch"
    )
    # And the fallback: both providers visible when the call errored.
    # Look for the conditional pattern that flips both flags to true on error.
    assert "settingsError ? true" in login, (
        "Login.tsx must default both localEnabled and ssoEnabled to TRUE on settingsError"
    )


def test_require_permission_used_for_admin_routes(scaffolded: Path) -> None:
    """App.tsx must wrap ALL 5 /settings/{users,roles,permissions,menus,auth}
    admin routes in <RequirePermission>. Phase C's existing test covers 4 —
    this one extends to /settings/permissions which Phase C omitted.
    Losing the guard on even one route is a silent escalation: any
    authenticated user could deep-link in and view/modify admin data.
    """
    app = (scaffolded / "frontend/src/App.tsx").read_text(encoding="utf-8")
    expected_paths = (
        "/settings/users",
        "/settings/roles",
        "/settings/permissions",
        "/settings/menus",
        "/settings/auth",
        "/settings/system-logs",
        "/settings/activity-logs",
    )
    for path in expected_paths:
        assert path in app, f"App.tsx must mount route {path}"
    # Each admin route must be inside a RequirePermission wrapper.
    # Find each path; require a `<RequirePermission` appears between
    # the route declaration and the closing `/>`.
    for path in expected_paths:
        idx = app.find(f'path="{path}"')
        assert idx != -1
        # Search forward up to 400 chars for a RequirePermission opener.
        window = app[idx:idx + 400]
        assert "<RequirePermission" in window, (
            f"App.tsx must wrap {path} in <RequirePermission perm=...> "
            "(any unguarded /settings/* admin route is a privilege regression)"
        )


def test_ai_call_logger_cost_estimate_is_wired(scaffolded: Path) -> None:
    """`_estimate_cost` must read pricing from AppSettingService, not stub
    to `return None`. Regression net for Phase 5 wiring — if a refactor
    drops the AppSettingService import, AI cost reporting silently dies."""
    text = (scaffolded / "backend/app/services/loggers/ai_call_logger.py").read_text(encoding="utf-8")
    assert "from app.services.app_setting_service import AppSettingService" in text, (
        "ai_call_logger.py must import AppSettingService for pricing lookup"
    )
    assert 'AppSettingService(self.db).get(f"ai.pricing.{model}")' in text, (
        "_estimate_cost must look up ai.pricing.<model> via AppSettingService"
    )


def test_registry_collect_yesterday_is_implemented(scaffolded: Path) -> None:
    """`collect_yesterday_metrics` must query DB tables, not return a stub.
    Without this, L3 registry telemetry is just `{date: ...}` and central
    monitoring/cost tracking is unreliable."""
    text = (scaffolded / "backend/app/integrations/registry.py").read_text(encoding="utf-8")
    # Each of the three log tables must be referenced.
    for model in ("AiCallLog", "ActivityLog", "SystemLog"):
        assert model in text, f"registry.py must query {model} for yesterday metrics"
    # The four AI metric keys the registry expects (docs/ops/registry.md §4.2).
    for key in ('"aiCalls"', '"aiInputTokens"', '"aiOutputTokens"', '"aiCostUsd"'):
        assert key in text, f"registry.py must emit {key} in telemetry payload"


def test_user_is_approved_column_emitted(scaffolded: Path) -> None:
    """Approval workflow — User model + UserSummary schema + frontend type
    must all carry is_approved, and the alembic migration must add the
    column with the right default policy (existing rows backfilled to
    true; new rows default false)."""
    user_model = (scaffolded / "backend/app/db/models/user.py").read_text(encoding="utf-8")
    assert "is_approved" in user_model, "User model must include is_approved column"

    schema = (scaffolded / "backend/app/schemas/auth.py").read_text(encoding="utf-8")
    s_start = schema.find("class UserSummary(")
    s_next = schema.find("\nclass ", s_start + 1)
    assert "is_approved" in schema[s_start:s_next], (
        "UserSummary must include is_approved (frontend table needs it)"
    )

    migrations = list((scaffolded / "backend/alembic/versions").glob("*0007_user_is_approved*"))
    assert migrations, "Migration 0007_user_is_approved must be emitted"
    m_text = migrations[0].read_text(encoding="utf-8")
    assert 'add_column' in m_text and 'is_approved' in m_text
    assert "UPDATE users SET is_approved = true" in m_text, (
        "Existing rows must be backfilled to True so bootstrap admin keeps logging in"
    )
    assert "auth.auto_approve_new_users" in m_text, (
        "Migration must seed the auto-approve setting"
    )

    ts_types = (scaffolded / "frontend/src/types/auth.ts").read_text(encoding="utf-8")
    ts_s_start = ts_types.find("export interface UserSummary")
    ts_s_next = ts_types.find("\nexport ", ts_s_start + 1)
    assert "isApproved" in ts_types[ts_s_start:ts_s_next], (
        "frontend UserSummary must include isApproved"
    )


def test_login_gate_rejects_unapproved_users(scaffolded: Path) -> None:
    """Login (local + SSO) must refuse unapproved accounts with a distinct
    error so the SPA can show 'awaiting approval' rather than 'wrong password'."""
    auth_router = (scaffolded / "backend/app/api/v1/auth.py").read_text(encoding="utf-8")
    assert auth_router.count('"account_not_approved"') >= 2, (
        "auth.py must raise account_not_approved in BOTH /login and /sso/callback"
    )
    assert "is_approved" in auth_router, "auth.py must check user.is_approved"
    assert 'auth.auto_approve_new_users' in auth_router, (
        "SSO JIT user creation must honour the auto-approve setting"
    )


def test_audit_coverage_completeness(scaffolded: Path) -> None:
    """Audit log gaps closed in Bug 11 — without these, the admin UI
    pages we just built would have blind spots.

    Gap 1 (scheduler → system_logs): _audit_job context manager wraps
    every background job so success/failure lands in system_logs (the
    page would otherwise stay empty even when jobs are firing).

    Gap 2 (permission denials): require_permission + require_any_permission
    log permission_denied to activity_logs BEFORE raising 403, so the
    Activity Logs Security tab actually shows attempted access.

    Gap 3 (CSV export auditing): the bulk-export endpoints log
    `export.system_logs` / `export.activity_logs` so "who downloaded a
    year of audit data" is itself in the audit trail.

    Gap 4 (token refresh): /auth/refresh logs `auth.refresh` so a
    stolen-token replay storm shows up in the Login tab.
    """
    sched = (scaffolded / "backend/app/core/scheduler.py").read_text(encoding="utf-8")
    assert "_audit_job" in sched and "SystemLogger" in sched, (
        "scheduler.py must wrap jobs in _audit_job(SystemLogger)"
    )

    deps = (scaffolded / "backend/app/auth/dependencies.py").read_text(encoding="utf-8")
    assert "_audit_permission_denied" in deps, (
        "dependencies.py must call _audit_permission_denied before raising 403"
    )
    assert '"auth.permission_denied"' in deps

    sysrouter = (scaffolded / "backend/app/api/v1/system_logs.py").read_text(encoding="utf-8")
    assert '"export.system_logs"' in sysrouter, (
        "system_logs export must self-audit"
    )

    actrouter = (scaffolded / "backend/app/api/v1/activity_logs.py").read_text(encoding="utf-8")
    assert '"export.activity_logs"' in actrouter, (
        "activity_logs export must self-audit"
    )

    auth = (scaffolded / "backend/app/api/v1/auth.py").read_text(encoding="utf-8")
    assert '"auth.refresh"' in auth, "refresh endpoint must audit"


def test_users_role_assignment_is_guarded(scaffolded: Path) -> None:
    """Privilege escalation guard — passing role_names through
    POST/PATCH /users must require roles.assign (and grants of
    internal:super_admin must come from an existing super-admin).
    Without these checks, anyone with users.update can hand themselves
    the super-admin role and the role-based access wall collapses."""
    users_router = (scaffolded / "backend/app/api/v1/users.py").read_text(encoding="utf-8")
    assert "_require_role_assign" in users_router, (
        "users.py must define a role-assignment guard helper"
    )
    # Helper invoked from BOTH paths that mutate role_names.
    assert users_router.count("_require_role_assign(user,") >= 2, (
        "guard must be called from both POST /users (create) and PATCH /users"
    )
    # Hierarchy check — only super-admin grants super-admin.
    assert '"internal:super_admin"' in users_router or "SUPER_ADMIN_ROLE" in users_router
    assert "roles.assign" in users_router, "guard must require the roles.assign perm"


def test_users_router_has_bulk_approve(scaffolded: Path) -> None:
    """Admin bulk-approve endpoint must be wired so the Users page button
    has a backend to call."""
    users_router = (scaffolded / "backend/app/api/v1/users.py").read_text(encoding="utf-8")
    assert "/bulk-approve" in users_router, "users.py must expose /bulk-approve"
    assert "BulkApproveRequest" in users_router


def test_activity_logs_page_wired_end_to_end(scaffolded: Path) -> None:
    """Activity Logs admin page — same pattern as System Logs, but reads
    activity_logs (login/logout/audit). Default UI filter is loginOnly."""
    backend_router = scaffolded / "backend/app/api/v1/activity_logs.py"
    assert backend_router.exists(), "activity_logs router must be emitted"
    installed = (scaffolded / "backend/app/api/v1/installed_routers.py").read_text(encoding="utf-8")
    assert "activity_logs_router" in installed and "/api/v1/admin/activity-logs" in installed

    seed = (scaffolded / "backend/app/seed.py").read_text(encoding="utf-8")
    assert '"activity_logs.read"' in seed
    assert '"settings.activity_logs"' in seed

    page = scaffolded / "frontend/src/pages/settings/ActivityLogs.tsx"
    assert page.exists()
    api = scaffolded / "frontend/src/api/activityLogs.ts"
    assert api.exists()

    app_tsx = (scaffolded / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert "/settings/activity-logs" in app_tsx
    assert "SettingsActivityLogs" in app_tsx


def test_system_logs_page_wired_end_to_end(scaffolded: Path) -> None:
    """System Logs admin page — must be emitted, routed, and the backend
    list endpoint must be mounted. Without any one of these the menu
    link is a 404."""
    backend_router = scaffolded / "backend/app/api/v1/system_logs.py"
    assert backend_router.exists(), "backend system_logs router must be emitted"
    installed = (scaffolded / "backend/app/api/v1/installed_routers.py").read_text(encoding="utf-8")
    assert "system_logs_router" in installed and "/api/v1/admin/system-logs" in installed, (
        "system_logs router must be wired in installed_routers.py"
    )

    seed = (scaffolded / "backend/app/seed.py").read_text(encoding="utf-8")
    assert '"system_logs.read"' in seed, "perm key must be seeded"
    assert '"settings.system_logs"' in seed, "menu row must be seeded"

    perm_enum = (scaffolded / "backend/app/auth/permissions.py").read_text(encoding="utf-8")
    assert "SYSTEM_LOGS_READ" in perm_enum

    frontend_page = scaffolded / "frontend/src/pages/settings/SystemLogs.tsx"
    assert frontend_page.exists(), "SystemLogs.tsx page must be emitted"
    frontend_api = scaffolded / "frontend/src/api/systemLogs.ts"
    assert frontend_api.exists(), "systemLogs.ts api client must be emitted"

    app_tsx = (scaffolded / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert "/settings/system-logs" in app_tsx, "route must be mounted in App.tsx"
    assert "SettingsSystemLogs" in app_tsx, "component must be imported"


def test_ui_store_emitted_for_sidebar_theme_locale(scaffolded: Path) -> None:
    """A2/A3/A4 — TopBar/Sidebar both read from the UI store. Without the
    store the buttons wire to nothing and the choice doesn't survive reload."""
    ui_store = scaffolded / "frontend/src/stores/ui.ts"
    assert ui_store.exists(), "frontend/src/stores/ui.ts must be emitted"
    text = ui_store.read_text(encoding="utf-8")
    for sym in ("toggleSidebar", "toggleTheme", "setLocale", "applyTheme", "applyLocale"):
        assert sym in text, f"ui store must expose {sym}"
    assert "localStorage" in text, "ui store must persist to localStorage"


def test_topbar_is_sticky_for_global_actions(scaffolded: Path) -> None:
    """TopBar must stay available while scrolling for menu, refresh, and locale actions."""
    text = (scaffolded / "frontend/src/components/Layout/TopBar.tsx").read_text(encoding="utf-8")
    assert "sticky top-0" in text, "TopBar header must remain pinned to the viewport top"
    assert "z-30" in text, "TopBar must layer above page content while scrolling"
    assert "RefreshCw" in text, "TopBar must include a refresh action"
    assert "window.location.reload()" in text, "TopBar refresh action must reload the current page"
    assert "common.refresh" in text, "TopBar refresh action must use localized accessible text"

    th = (scaffolded / "frontend/src/i18n/locales/th.json").read_text(encoding="utf-8")
    en = (scaffolded / "frontend/src/i18n/locales/en.json").read_text(encoding="utf-8")
    assert '"refresh": "รีเฟรช"' in th
    assert '"refresh": "Refresh"' in en


def test_app_layout_is_fixed_height_shell(scaffolded: Path) -> None:
    """AppLayout must be a fixed-height, overflow-hidden shell so the sidebar and
    main scroll independently instead of the whole page — and the sidebar/main row
    must carry min-h-0 so flex children can shrink and scroll rather than clip."""
    text = (scaffolded / "frontend/src/components/Layout/AppLayout.tsx").read_text(encoding="utf-8")
    assert "h-dvh" in text, "shell must use the dynamic viewport height"
    assert "overflow-hidden" in text, "shell itself must not scroll; only its panes do"
    assert "min-h-0" in text, "the sidebar/main row needs min-h-0 or flex children clip instead of scrolling"
    assert "overflow-y-auto" in text, "main must own the page scroll"


def test_sidebar_nav_scrolls_within_shell(scaffolded: Path) -> None:
    """Sidebar nav must scroll internally so long menu trees never clip in the
    fixed-height shell — on the desktop rail and the mobile drawer alike."""
    text = (scaffolded / "frontend/src/components/Layout/Sidebar.tsx").read_text(encoding="utf-8")
    assert "flex-col" in text, "aside must be a flex column so its nav can flex-fill the height"
    assert "min-h-0 flex-1" in text and "overflow-y-auto" in text, (
        "nav must be flex-1 min-h-0 overflow-y-auto to scroll long menu lists internally"
    )
    assert "calc(100vh" not in text, (
        "drop the fragile magic-number height (it broke when TopBar height changed) — "
        "the sidebar now fills the shell row via flex"
    )


def test_seed_app_settings_includes_pricing_and_retention(scaffolded: Path) -> None:
    """Seed must populate the keys the runtime now reads — otherwise
    fresh installs ship with NULL cost and default retention forever."""
    text = (scaffolded / "backend/app/seed.py").read_text(encoding="utf-8")
    for key in (
        '"ai.pricing.claude-sonnet-4-6"',
        '"ai.pricing.claude-haiku-4-5-20251001"',
        '"logging.activity.retention_days"',
        '"logging.system.retention_days"',
        '"logging.ai.retention_days"',
    ):
        assert key in text, f"_seed_app_settings must seed {key}"


def test_seed_uses_selectinload_and_refresh_for_relationships(scaffolded: Path) -> None:
    """Bug 1 — _seed_roles / _seed_bootstrap_user must avoid the
    MissingGreenlet async lazy-load trap by eager-loading the
    relationship and refreshing freshly-flushed rows before assigning."""
    text = (scaffolded / "backend/app/seed.py").read_text(encoding="utf-8")
    assert "from sqlalchemy.orm import selectinload" in text, (
        "seed.py must import selectinload"
    )
    assert "selectinload(Role.permissions)" in text, (
        "_seed_roles must eager-load Role.permissions"
    )
    assert 'await db.refresh(existing, attribute_names=["permissions"])' in text, (
        "_seed_roles must refresh newly-flushed Role before assigning permissions"
    )
    assert 'await db.refresh(user, attribute_names=["roles"])' in text, (
        "_seed_bootstrap_user must refresh newly-flushed User before assigning roles"
    )


def test_seed_loads_dotenv(scaffolded: Path) -> None:
    """Bug 2 — seed reads AUTH_BOOTSTRAP_* via os.getenv, but pydantic-settings
    only loads .env into the Settings object, not os.environ. seed.py must
    explicitly call load_dotenv() or it silently no-ops the bootstrap user."""
    text = (scaffolded / "backend/app/seed.py").read_text(encoding="utf-8")
    assert "load_dotenv" in text, (
        "seed.py must call load_dotenv() so os.getenv sees AUTH_BOOTSTRAP_* from .env"
    )


def test_env_references_app_seed_module_not_db_seed_stub(scaffolded: Path) -> None:
    """Bug 3 — backend/.env had a comment pointing users at
    `python -m app.db.seed` which is a no-op stub. The real bootstrap
    module is `app.seed`."""
    text = (scaffolded / "backend/.env").read_text(encoding="utf-8")
    assert "python -m app.db.seed" not in text, (
        ".env must not reference the stub `app.db.seed` — use `app.seed` instead"
    )
    assert "python -m app.seed" in text, (
        ".env should tell users to run `python -m app.seed`"
    )


def test_docker_compose_port_mapping_uses_db_port(tmp_path: Path) -> None:
    """Bug 4 — _scaffold_docker must template the host-side port from
    db_port so the compose mapping matches what .env tells the backend
    to dial. Hardcoding `5432:5432` causes a connect-refuse whenever
    the wizard had to pick a different host port."""
    from scaffold import _scaffold_docker  # noqa: E402

    cfg = _cfg(project_slug="port-test")
    _scaffold_docker(cfg, tmp_path, db_port=5439)
    compose = (tmp_path / "docker/docker-compose.yml").read_text(encoding="utf-8")
    # Port now binds to 127.0.0.1 to keep dev DB off the LAN (audit #11).
    assert '"127.0.0.1:5439:5432"' in compose, (
        "compose host-port mapping must use db_port arg + bind 127.0.0.1"
    )


def test_users_summary_includes_roles_end_to_end(scaffolded: Path) -> None:
    """Bug 7 — UserSummary (the LIST endpoint payload) was missing `roles`,
    so the admin Users page couldn't show what role each user has.
    Roles must flow through: backend schema → list endpoint selectinload
    → frontend type → admin table column."""
    schema = (scaffolded / "backend/app/schemas/auth.py").read_text(encoding="utf-8")
    # roles must be on UserSummary (not just UserRead) — anchor on the class
    # definition with surrounding context.
    summary_start = schema.find("class UserSummary(")
    next_class = schema.find("\nclass ", summary_start + 1)
    summary_body = schema[summary_start:next_class]
    assert "roles: list[RoleSummary]" in summary_body, (
        "UserSummary (list endpoint) must include roles"
    )

    users_router = (scaffolded / "backend/app/api/v1/users.py").read_text(encoding="utf-8")
    assert "selectinload(User.roles)" in users_router, (
        "list_users must eager-load User.roles or the JSON will be missing them"
    )

    ts_types = (scaffolded / "frontend/src/types/auth.ts").read_text(encoding="utf-8")
    ts_summary_start = ts_types.find("export interface UserSummary")
    ts_next = ts_types.find("\nexport ", ts_summary_start + 1)
    ts_summary_body = ts_types[ts_summary_start:ts_next]
    assert "roles" in ts_summary_body, (
        "frontend UserSummary interface must include roles"
    )

    users_page = (scaffolded / "frontend/src/pages/settings/Users.tsx").read_text(encoding="utf-8")
    assert "settings.users.fields.roles" in users_page, (
        "Users.tsx must render a Role column (i18n key settings.users.fields.roles)"
    )
    assert "colSpan={8}" in users_page, (
        "empty-state colSpan must match the 8-column layout (email/name/provider/roles/approved/active/lastLogin/actions)"
    )


# ---------------------------------------------------------------------------
# v3.0.20 — cross-layer consistency review (6 issues).
#
# Each test below pins one fix so a future template edit can't silently
# re-break the generate -> boot -> use path. Issue 1's backend half is
# covered by test_provider_toggle_respects_auth_scope_ceiling above (which
# now asserts the CORRECTED lock direction); the tests here cover the other
# layers + issues 2-6. See PROGRESS.md v3.0.20.
# ---------------------------------------------------------------------------


def test_auth_scope_ceiling_direction_seed_and_frontend(scaffolded: Path) -> None:
    """Issue 1 — the ceiling must lock the SAME provider across all three
    layers (backend map, seed defaults, SPA gating):
        internal_only (Azure-only) -> local locked OFF
        external_only (local-only) -> SSO   locked OFF
    The backend map is asserted elsewhere; here we pin seed + frontend so a
    one-layer drift (which is exactly how the original bug hid) can't pass.
    """
    seed = (scaffolded / "backend/app/seed.py").read_text(encoding="utf-8")
    assert 'scope != "internal_only"' in seed, (
        "seed: auth.local.enabled default must be `scope != internal_only` "
        "(local on UNLESS Azure-only)"
    )
    assert 'scope != "external_only"' in seed, (
        "seed: auth.sso.enabled default must be `scope != external_only` "
        "(sso on UNLESS local-only)"
    )

    page = (scaffolded / "frontend/src/pages/settings/AuthSettings.tsx").read_text(encoding="utf-8")
    assert "localLockedOff = SCOPE === 'internal_only'" in page, (
        "AuthSettings: local must be the toggle locked off for internal_only"
    )
    assert "ssoLockedOff = SCOPE === 'external_only'" in page, (
        "AuthSettings: sso must be the toggle locked off for external_only"
    )


def test_init_project_backend_env_canonical_and_has_auth_scope(tmp_path: Path) -> None:
    """Issue 2 — even with ENV_VAR_PREFIX set, init_project must emit
    CANONICAL (un-prefixed) backend env keys. The backend reads them via
    pydantic Settings (no env_prefix) AND raw os.getenv (AUTH_SCOPE,
    AUTH_BOOTSTRAP_*), so a prefixed key = required field missing = boot
    crash. Also pins the AUTH_SCOPE parity gap (init_project used to omit it
    entirely, so the ceiling silently defaulted to 'both').
    """
    import init_project  # noqa: E402

    cfg = init_project.ProjectConfig(
        project_slug="prefix-test",
        env_var_prefix="CTV",
        # external_only keeps the local-auth JWT block (so we can assert a
        # second canonical key) while still using a non-default AUTH_SCOPE.
        auth_scope="external_only",
    )
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    init_project.generate_env_files(cfg, tmp_path)

    env = (tmp_path / "backend/.env").read_text(encoding="utf-8")
    # No key may carry the prefix — this is the boot-blocker guard.
    assert "CTV_" not in env, (
        "init_project must NOT prefix .env keys (the backend reads canonical "
        "names via Settings + os.getenv; a prefix breaks boot)"
    )
    # Canonical required keys present.
    assert "\nDB_PASSWORD=" in env, "backend/.env must define canonical DB_PASSWORD"
    assert "\nJWT_SECRET_KEY=" in env, "backend/.env must define canonical JWT_SECRET_KEY"
    # AUTH_SCOPE must be present + correct (read by admin_settings.py).
    assert "AUTH_SCOPE=external_only" in env, (
        "init_project backend/.env must write AUTH_SCOPE so the ceiling is enforced"
    )


def test_init_project_emits_jwt_secret_for_internal_only(tmp_path: Path) -> None:
    """Issue 7 (found during review) — Settings.JWT_SECRET_KEY is REQUIRED
    (no default), and the app signs its own session token even for SSO
    logins. init_project used to gate the JWT block behind
    auth_scope in (both, external_only), so an `internal_only` project shipped
    a .env with no JWT_SECRET_KEY and crashed on boot with a pydantic
    ValidationError. scaffold.py always emits it; init_project must too.
    """
    import init_project  # noqa: E402

    cfg = init_project.ProjectConfig(project_slug="sso-only", auth_scope="internal_only")
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    init_project.generate_env_files(cfg, tmp_path)

    env = (tmp_path / "backend/.env").read_text(encoding="utf-8")
    assert "\nJWT_SECRET_KEY=" in env, (
        "internal_only projects must still emit JWT_SECRET_KEY (required by "
        "Settings; signs the app session token even for SSO)"
    )


def test_init_project_frontend_env_has_vite_auth_scope_and_app_name(tmp_path: Path) -> None:
    """Issue 3 — init_project's frontend/.env must set VITE_AUTH_SCOPE (it
    overwrites the scaffold's copy with write_text), or AuthSettings.tsx
    falls back to 'both' and stops enforcing the ceiling in the UI. It
    must also preserve VITE_APP_NAME for the TopBar title."""
    import init_project  # noqa: E402

    cfg = init_project.ProjectConfig(
        project_slug="vite-test",
        project_display_name="Vendor Portal",
        auth_scope="external_only",
    )
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    init_project.generate_env_files(cfg, tmp_path)

    fe = (tmp_path / "frontend/.env").read_text(encoding="utf-8")
    assert "VITE_AUTH_SCOPE=external_only" in fe, (
        "init_project frontend/.env must mirror VITE_AUTH_SCOPE (parity with setup.py)"
    )
    assert "VITE_APP_NAME=Chia Tai – Vendor Portal" in fe


def test_list_users_supports_q_search(scaffolded: Path) -> None:
    """Issue 4 — the SPA's Users page sends ?q=; list_users must accept it
    and ilike-filter on email + full_name, else the search box is a silent
    no-op (results never narrow)."""
    import re

    users_router = (scaffolded / "backend/app/api/v1/users.py").read_text(encoding="utf-8")
    m = re.search(r"async def list_users\((.*?)\)\s*->", users_router, re.S)
    assert m and "q:" in m.group(1), "list_users must accept a `q` query param"
    assert "User.email.ilike" in users_router and "User.full_name.ilike" in users_router, (
        "list_users must ilike-filter q across email AND full_name"
    )


def test_settings_index_includes_log_cards(scaffolded: Path) -> None:
    """Issue 5 — SettingsIndex must surface System Logs + Activity Logs cards
    (their routes + seeded menu rows already exist), each gated by its read
    permission. Without the cards the only way in is the sidebar menu, so the
    /settings landing page misrepresents what's available."""
    idx = (scaffolded / "frontend/src/pages/SettingsIndex.tsx").read_text(encoding="utf-8")
    for to, perm in (
        ("/settings/system-logs", "system_logs.read"),
        ("/settings/activity-logs", "activity_logs.read"),
    ):
        assert to in idx, f"SettingsIndex must link to {to}"
        assert perm in idx, f"SettingsIndex {to} card must be gated by {perm}"


def test_create_role_validates_name_scope_prefix(scaffolded: Path) -> None:
    """Issue 6 — the API boundary (not just the SPA's zod schema) must enforce
    that a role name's prefix matches its provider_scope. The validator must
    be defined AND called from create_role, and cover all three scopes."""
    roles_router = (scaffolded / "backend/app/api/v1/roles.py").read_text(encoding="utf-8")
    # Defined (1) + called from create_role (1) => at least 2 references.
    assert roles_router.count("_validate_role_name_scope(") >= 2, (
        "roles.py must define _validate_role_name_scope AND call it in create_role"
    )
    for token in (
        'provider_scope == "internal"',
        'provider_scope == "external"',
        'provider_scope == "any"',
    ):
        assert token in roles_router, f"role validator must handle {token}"
    # It must reject (HTTP 400) on mismatch.
    assert "status_code=400" in roles_router, (
        "role validator must reject mismatches with HTTP 400"
    )

