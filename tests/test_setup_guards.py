"""Guards for scripts/setup.py target-dir resolution.

Sibling-zip layout can cause setup.py to scaffold INTO web-app-standard/
itself (because `script_path.parent.parent` resolves to the template repo
when invoked from a sibling project dir).

These tests verify:
 1. setup.py refuses to scaffold into a dir that looks like the
    web-app-standard template itself (even when explicitly passed).
 2. --project-dir is honored and points scaffolding away from
    script_path.parent.parent.

We test the helpers + a subprocess invocation. End-to-end setup.py
is not exercised here (interactive + heavy); we stop right after the
target-dir guard fires.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
SETUP_PY = SCRIPTS / "setup.py"

sys.path.insert(0, str(SCRIPTS))


def _utf8_env() -> dict[str, str]:
    """Force utf-8 stdio so Windows cp1252 doesn't choke on Thai / em-dashes."""
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


# ───────────────────────────────────────────────────────────────
# Unit tests for helpers
# ───────────────────────────────────────────────────────────────


def test_is_template_dir_detects_real_template() -> None:
    """The actual web-app-standard repo (where these tests live) IS the
    canonical upstream — it has the template-shape AND a .git/config
    pointing at CT-IT-Center/WEB-APP-STANDARD."""
    from setup import _is_template_dir  # noqa: PLC0415

    assert _is_template_dir(REPO_ROOT), (
        "the actual web-app-standard repo root should be detected as template"
    )


def test_is_template_dir_rejects_empty_dir(tmp_path: Path) -> None:
    from setup import _is_template_dir  # noqa: PLC0415

    assert not _is_template_dir(tmp_path)


def test_is_template_dir_rejects_partial_match(tmp_path: Path) -> None:
    """Dir with just AGENTS.md (no scripts/setup.py) is not the template."""
    from setup import _is_template_dir  # noqa: PLC0415

    (tmp_path / "AGENTS.md").write_text("# stub", encoding="utf-8")
    assert not _is_template_dir(tmp_path)


def test_is_template_dir_rejects_layout_a_copy(tmp_path: Path) -> None:
    """REGRESSION (D1): a Layout-A copy made by `robocopy ... /XD .git`
    has the template-shape files (AGENTS.md, scripts/setup.py,
    scripts/scaffold.py) but no .git/. It must NOT be detected as the
    canonical upstream — otherwise the main() guard refuses to scaffold
    into the user's own project copy, which was Sprint 4 Bug D1."""
    from setup import _is_template_dir  # noqa: PLC0415

    (tmp_path / "AGENTS.md").write_text("# stub", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "setup.py").write_text("# stub", encoding="utf-8")
    (tmp_path / "scripts" / "scaffold.py").write_text("# stub", encoding="utf-8")
    # NO .git directory — mirrors `robocopy /XD .git` output

    assert not _is_template_dir(tmp_path), (
        "Layout-A copy (template-shape + no .git) must NOT be flagged as upstream"
    )


# ───────────────────────────────────────────────────────────────
# Subprocess executable resolution + graceful-failure (dogfood 2026-06-01)
# ───────────────────────────────────────────────────────────────


def test_resolve_argv_resolves_and_passes_through() -> None:
    """REGRESSION: `npm` ships as `npm.CMD` on Windows; CreateProcess
    (shell=False) does NOT search PATHEXT, so a bare ["npm", ...] raised
    FileNotFoundError and aborted the wizard at the frontend-install step on
    every Windows machine. _resolve_argv must turn a resolvable exe into a
    full path (which subprocess CAN launch with shell=False) while leaving
    str commands and unresolvable names untouched."""
    import shutil  # noqa: PLC0415

    from setup import _resolve_argv  # noqa: PLC0415

    # str (shell=True) returned unchanged
    assert _resolve_argv("npm install") == "npm install"
    # unresolvable exe left as-is so the caller still sees the failure
    assert _resolve_argv(["__no_such_bin__", "x"]) == ["__no_such_bin__", "x"]
    # resolvable exe -> absolute path
    if shutil.which("python"):
        resolved = _resolve_argv(["python", "-V"])
        assert os.path.isabs(resolved[0]), f"expected abs path, got {resolved[0]}"


def test_run_helpers_do_not_raise_on_missing_exe() -> None:
    """A missing executable must yield a non-zero code, never raise — so a
    failed `npm install` degrades gracefully instead of crashing setup
    before DB/migrate/seed run."""
    from setup import _run, _run_visible  # noqa: PLC0415

    assert _run_visible(["__no_such_bin__"]) != 0
    code, _out = _run(["__no_such_bin__"])
    assert code != 0


def test_spawn_backend_cmd_has_no_trailing_space_before_amp() -> None:
    """REGRESSION (dogfood 2026-06-01): the wizard opened the backend window
    with `set DB_HOST=localhost && ...`. In cmd.exe that stores the value
    INCLUDING the trailing space → DB_HOST='localhost ' → asyncpg
    getaddrinfo('localhost ') fails → every DB query (login) returns 500,
    surfaced in the SPA as 'Incorrect email or password'. /health still works
    (no DB), which made it look like a credential problem. The spawned-server
    command must use `set VAR=value&&` (no space before &&)."""
    src = SETUP_PY.read_text(encoding="utf-8")
    assert "set DB_HOST=localhost &&" not in src, (
        "space before && makes cmd store DB_HOST='localhost ' → getaddrinfo fails"
    )
    assert "set DB_HOST=localhost&&" in src, (
        "expected the no-trailing-space cmd form `set DB_HOST=localhost&&`"
    )


def test_is_template_dir_detects_canonical_upstream_with_git(tmp_path: Path) -> None:
    """Template-shape PLUS .git/config containing 'web-app-standard'
    IS the canonical upstream — refuse to scaffold."""
    from setup import _is_template_dir  # noqa: PLC0415

    (tmp_path / "AGENTS.md").write_text("# stub", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "setup.py").write_text("# stub", encoding="utf-8")
    (tmp_path / "scripts" / "scaffold.py").write_text("# stub", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        '[remote "origin"]\n'
        '    url = https://github.com/CT-IT-Center/WEB-APP-STANDARD.git\n',
        encoding="utf-8",
    )

    assert _is_template_dir(tmp_path)


def test_is_template_dir_rejects_fork_with_unrelated_remote(tmp_path: Path) -> None:
    """A fork/clone with an unrelated remote URL is NOT the canonical
    upstream — user is free to scaffold into it."""
    from setup import _is_template_dir  # noqa: PLC0415

    (tmp_path / "AGENTS.md").write_text("# stub", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "setup.py").write_text("# stub", encoding="utf-8")
    (tmp_path / "scripts" / "scaffold.py").write_text("# stub", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        '[remote "origin"]\n'
        '    url = https://github.com/some-user/my-project.git\n',
        encoding="utf-8",
    )

    assert not _is_template_dir(tmp_path)


def test_looks_like_fresh_project_dir(tmp_path: Path) -> None:
    from setup import _looks_like_fresh_project_dir  # noqa: PLC0415

    assert _looks_like_fresh_project_dir(tmp_path), "empty dir is fresh"

    (tmp_path / "README.md").write_text("# stub", encoding="utf-8")
    assert _looks_like_fresh_project_dir(tmp_path), "README.md only is still fresh"

    (tmp_path / "backend").mkdir()
    assert not _looks_like_fresh_project_dir(tmp_path), (
        "backend/ subfolder means it's a scaffolded project, not fresh"
    )


# ───────────────────────────────────────────────────────────────
# Subprocess: setup.py must refuse when target is the template
# ───────────────────────────────────────────────────────────────


def test_setup_refuses_template_dir_as_project_dir() -> None:
    """Invoking setup.py with --project-dir pointing at the template
    itself must exit non-zero with a clear error."""
    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_PY),
            "--project-dir",
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
        timeout=30,
        # stdin closed → no interactive prompt can hang the test
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0, (
        f"expected non-zero exit, got 0\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    # Error must mention the template / web-app-standard clearly
    assert "template" in combined.lower() or "web-app-standard" in combined.lower(), (
        f"error message should mention template / web-app-standard:\n{combined}"
    )


def test_setup_honors_project_dir_flag(tmp_path: Path) -> None:
    """setup.py with --project-dir <fresh-dir> must target THAT dir, not
    script_path.parent.parent. We verify by detecting that the guard
    DOES NOT fire (i.e. target is accepted) — we close stdin so the
    interactive collect_config() step exits immediately and the script
    fails further down, but with a different exit path than the template-
    dir guard."""
    fresh = tmp_path / "my-app"
    fresh.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_PY),
            "--project-dir",
            str(fresh),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
        timeout=30,
        stdin=subprocess.DEVNULL,
    )
    combined = result.stdout + result.stderr

    # Must NOT contain the template-dir refusal message
    assert "target dir เป็น web-app-standard template เอง" not in combined, (
        f"setup.py wrongly treated {fresh} as a template:\n{combined}"
    )
    assert "--project-dir ชี้ไปที่ web-app-standard template" not in combined, (
        f"setup.py wrongly treated {fresh} as a template:\n{combined}"
    )
    # The fresh dir should be empty after the early-exit (we never reach scaffold)
    # — guarantees we didn't scaffold into the wrong place.
    assert not (REPO_ROOT / "project.config").exists(), (
        "setup.py wrote project.config into the template repo — Bug 1 regressed!"
    )


def test_setup_refuses_explicit_template_target_with_helpful_message() -> None:
    """The error message should mention BOTH layouts so user knows the fix."""
    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_PY),
            "--project-dir",
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
        timeout=30,
        stdin=subprocess.DEVNULL,
    )
    combined = result.stdout + result.stderr
    # Should mention Layout A and sibling-zip both
    assert "Layout A" in combined, (
        f"error should reference Layout A:\n{combined}"
    )
    assert "Sibling-zip" in combined or "sibling-zip" in combined.lower(), (
        f"error should reference sibling-zip:\n{combined}"
    )


# ───────────────────────────────────────────────────────────────
# D2: utf-8 stdio on Windows cp1252
# ───────────────────────────────────────────────────────────────


def test_ensure_utf8_stdio_is_idempotent_on_non_win32() -> None:
    """On non-win32, _ensure_utf8_stdio is a no-op — must not raise."""
    from setup import _ensure_utf8_stdio  # noqa: PLC0415

    _ensure_utf8_stdio()  # should not raise on any platform


def test_ensure_utf8_stdio_reconfigures_on_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    """On simulated win32, _ensure_utf8_stdio calls stream.reconfigure with
    encoding='utf-8'."""
    from setup import _ensure_utf8_stdio  # noqa: PLC0415

    calls: list[dict] = []

    class FakeStream:
        def reconfigure(self, **kwargs: object) -> None:
            calls.append(kwargs)

    fake_stdout = FakeStream()
    fake_stderr = FakeStream()

    monkeypatch.setattr("setup.sys.platform", "win32")
    monkeypatch.setattr("setup.sys.stdout", fake_stdout)
    monkeypatch.setattr("setup.sys.stderr", fake_stderr)

    _ensure_utf8_stdio()

    assert len(calls) == 2
    for call in calls:
        assert call.get("encoding") == "utf-8"
        assert call.get("errors") == "replace"


def test_ensure_utf8_stdio_swallows_attribute_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If stream has no reconfigure (e.g. piped to non-TextIOWrapper),
    _ensure_utf8_stdio must not crash."""
    from setup import _ensure_utf8_stdio  # noqa: PLC0415

    class StreamWithoutReconfigure:
        pass  # no reconfigure attribute

    monkeypatch.setattr("setup.sys.platform", "win32")
    monkeypatch.setattr("setup.sys.stdout", StreamWithoutReconfigure())
    monkeypatch.setattr("setup.sys.stderr", StreamWithoutReconfigure())

    _ensure_utf8_stdio()  # must not raise


@pytest.mark.skipif(sys.platform != "win32", reason="cp1252 stdio is a Windows issue")
def test_setup_help_does_not_crash_on_cp1252() -> None:
    """REGRESSION (D2): `python scripts/setup.py --help` must not crash
    when stdout is cp1252 (typical Windows PowerShell default).
    PYTHONLEGACYWINDOWSSTDIO=1 forces Python to use the OS codepage
    instead of utf-8 — simulates the original crash environment."""
    env = {**os.environ, "PYTHONLEGACYWINDOWSSTDIO": "1"}
    # Drop PYTHONIOENCODING so it doesn't override the legacy flag
    env.pop("PYTHONIOENCODING", None)
    result = subprocess.run(
        [sys.executable, str(SETUP_PY), "--help"],
        capture_output=True,
        env=env,
        timeout=15,
        stdin=subprocess.DEVNULL,
    )
    # On win32 with cp1252, --help should still exit 0 (argparse output
    # is ASCII so even pre-fix would survive — the real value is that
    # the import + _ensure_utf8_stdio path doesn't blow up).
    assert result.returncode == 0, (
        f"--help should exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )




def test_stdio_helper_is_shared_module() -> None:
    """F5: ensure_utf8_stdio lives in scripts/_stdio.py and is importable
    by all three CLI scripts. Verifies the extract-to-shared-module
    refactor — guards against someone copy-pasting it back inline."""
    from _stdio import ensure_utf8_stdio  # noqa: PLC0415

    # Must be a callable and idempotent
    ensure_utf8_stdio()
    ensure_utf8_stdio()


def test_scaffold_emits_auth_frontend_base_url_in_settings() -> None:
    """F6: scripts/scaffold.py must emit AUTH_FRONTEND_BASE_URL in the
    generated Settings class so the auth module's Bug 10 fix can read it
    via `getattr(settings, 'AUTH_FRONTEND_BASE_URL', None)`."""
    scaffold_src = (SCRIPTS / "scaffold.py").read_text(encoding="utf-8")

    # Settings class field
    assert "AUTH_FRONTEND_BASE_URL: str" in scaffold_src, (
        "scaffold.py must declare AUTH_FRONTEND_BASE_URL in the Settings class"
    )


def test_scaffold_emits_auth_frontend_base_url_in_env_files() -> None:
    """F6: scripts/scaffold.py must emit AUTH_FRONTEND_BASE_URL in BOTH
    the generated backend/.env and backend/.env.example so users have a
    starting value (http://localhost:5173) they can edit."""
    scaffold_src = (SCRIPTS / "scaffold.py").read_text(encoding="utf-8")

    # Counts occurrences — should be at least 2 (env + env.example) + 1 (Settings class)
    occurrences = scaffold_src.count("AUTH_FRONTEND_BASE_URL")
    assert occurrences >= 3, (
        f"AUTH_FRONTEND_BASE_URL appears {occurrences} times in scaffold.py; "
        f"expected at least 3 (Settings field + .env + .env.example)"
    )
    # And the .env path should pre-fill the dev SPA URL
    assert "AUTH_FRONTEND_BASE_URL=http://localhost:5173" in scaffold_src, (
        "scaffold.py should default AUTH_FRONTEND_BASE_URL=http://localhost:5173 in .env"
    )


# ───────────────────────────────────────────────────────────────
# F1 / F2 — identifier validation + existence checks
# ───────────────────────────────────────────────────────────────


def test_validate_ident_accepts_normal_names() -> None:
    from setup import _validate_ident  # noqa: PLC0415

    assert _validate_ident("db_user", "my_app") == "my_app"
    assert _validate_ident("db_user", "_private") == "_private"
    assert _validate_ident("db_user", "App123") == "App123"


def test_validate_ident_rejects_injection_attempts() -> None:
    """F1: identifier with `;` / `'` / space must be refused before any
    SQL is composed."""
    from setup import _validate_ident  # noqa: PLC0415

    bad_inputs = [
        "'; DROP DATABASE postgres; --",
        "user with space",
        "user;drop",
        "1user",  # starts with digit
        "user-name",  # hyphens reserved for quoted ident only
        "",
    ]
    for bad in bad_inputs:
        with pytest.raises(SystemExit):
            _validate_ident("db_user", bad)


def test_sql_quote_literal_doubles_single_quotes() -> None:
    """F1: passwords with `'` must escape via SQL doubled-quote (`''`).
    Backslashes are refused outright to avoid mixing with E'...' strings."""
    from setup import _sql_quote_literal  # noqa: PLC0415

    assert _sql_quote_literal("simple") == "simple"
    assert _sql_quote_literal("it's-secret") == "it''s-secret"
    assert _sql_quote_literal("''double''") == "''''double''''"


def test_sql_quote_literal_rejects_backslash() -> None:
    from setup import _sql_quote_literal  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _sql_quote_literal("has\\backslash")


# ───────────────────────────────────────────────────────────────
# D3: Docker engine off + native pg on 5432 → soft warn, not hard fail
# ───────────────────────────────────────────────────────────────


def test_setup_proceeds_when_docker_off_but_native_pg_present() -> None:
    """REGRESSION (D3): handle_env_issues must return True when Docker
    engine is off BUT port 5432 has a native PostgreSQL listener."""
    from setup import EnvCheck, handle_env_issues  # noqa: PLC0415

    env = EnvCheck(
        docker_cli_ok=True,
        docker_engine_ok=False,
        docker_ok=False,
        node_ok=True,
        node_version="v20.0.0",
        python_ok=True,
        port_conflicts=[5432],  # native pg listening
        ports_ok={5432: False, 8000: True, 5173: True},
    )

    assert handle_env_issues(env) is True


def test_setup_blocks_when_docker_off_and_no_native_pg() -> None:
    """REGRESSION-protect (D3): without native pg AND without Docker
    engine, we still hard-fail — that's the original Bug-3 behavior we
    want to preserve."""
    from setup import EnvCheck, handle_env_issues  # noqa: PLC0415

    env = EnvCheck(
        docker_cli_ok=True,
        docker_engine_ok=False,
        docker_ok=False,
        node_ok=True,
        node_version="v20.0.0",
        python_ok=True,
        port_conflicts=[],
        ports_ok={5432: True, 8000: True, 5173: True},
    )

    assert handle_env_issues(env) is False


def test_setup_blocks_when_docker_cli_missing() -> None:
    """Docker CLI must exist regardless of native pg — without it,
    find_postgres_containers() would crash trying to call `docker ps`."""
    from setup import EnvCheck, handle_env_issues  # noqa: PLC0415

    env = EnvCheck(
        docker_cli_ok=False,
        docker_engine_ok=False,
        docker_ok=False,
        node_ok=True,
        node_version="v20.0.0",
        python_ok=True,
        port_conflicts=[5432],
        ports_ok={5432: False, 8000: True, 5173: True},
    )

    assert handle_env_issues(env) is False


def test_existing_database_mode_does_not_require_docker() -> None:
    from setup import EnvCheck, handle_env_issues  # noqa: PLC0415

    env = EnvCheck(
        docker_cli_ok=False,
        docker_engine_ok=False,
        docker_ok=False,
        node_ok=True,
        python_ok=True,
    )

    assert handle_env_issues(env, database_mode="existing") is True


def test_database_mode_defaults_to_new() -> None:
    from setup import _parse_args  # noqa: PLC0415

    assert _parse_args([]).database_mode == "new"
    assert _parse_args(["--database-mode", "existing"]).database_mode == "existing"


def test_existing_database_batch_uses_explicit_mode_without_docker_gate() -> None:
    batch = (REPO_ROOT / "setup-existing-db.bat").read_text(encoding="utf-8")

    assert "python scripts\\setup.py --database-mode existing" in batch
    assert "docker --version" not in batch


def test_choose_existing_database_marks_schema_setup_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from setup import choose_existing_database  # noqa: PLC0415

    answers = iter(["db.internal", "5433", "legacy_db", "app_reader"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr("setup._getpass_masked", lambda _prompt="": "secret")

    result = choose_existing_database("my_app")

    assert result == {
        "skip": False,
        "database_mode": "existing",
        "existing_database": True,
        "skip_schema_setup": True,
        "host": "db.internal",
        "port": 5433,
        "db_name": "legacy_db",
        "db_user": "app_reader",
        "db_password": "secret",
    }


def test_existing_database_password_stays_out_of_docker_compose(
    tmp_path: Path,
) -> None:
    from setup import ProjectConfig, generate_files  # noqa: PLC0415

    cfg = ProjectConfig(
        project_slug="legacy-app",
        project_display_name="Legacy App",
    )
    choice = {
        "skip": False,
        "database_mode": "existing",
        "existing_database": True,
        "skip_schema_setup": True,
        "host": "db.internal",
        "port": 5432,
        "db_name": "legacy",
        "db_user": "app_reader",
        "db_password": "existing-secret",
    }

    generate_files(cfg, tmp_path, choice)

    backend_env = (tmp_path / "backend" / ".env").read_text(encoding="utf-8")
    compose = (tmp_path / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    project_config = (tmp_path / "project.config").read_text(encoding="utf-8")

    assert "DB_PASSWORD=existing-secret" in backend_env
    assert "existing-secret" not in compose
    assert "DATABASE_MODE=existing" in project_config


def test_choose_database_routes_to_native_pg_when_docker_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Docker engine is off but native pg is present, choose_database
    skips find_postgres_containers (no engine = crash/empty anyway) and
    goes straight to the native-pg branch."""
    from setup import EnvCheck, choose_database  # noqa: PLC0415

    env = EnvCheck(
        docker_cli_ok=True,
        docker_engine_ok=False,
        docker_ok=False,
        node_ok=True,
        python_ok=True,
        port_conflicts=[5432],
    )

    # Stub interactive prompts: confirm need_db=yes, no edit credentials
    answers = iter([
        "y",         # need DB?
        "postgres",  # pg superuser
        "n",         # edit credentials? -> no
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    # find_postgres_containers must NOT be called (would crash on no engine)
    def boom() -> list:
        raise AssertionError(
            "find_postgres_containers must not be called on native-pg path"
        )

    monkeypatch.setattr("setup.find_postgres_containers", boom)

    result = choose_database("my_app", env=env)

    assert result["skip"] is False
    assert result.get("native") is True
    assert result["use_existing"] is True
    assert result["host"] == "localhost"
    assert result["port"] == 5432
    assert result["pg_user"] == "postgres"
    assert result["db_name"] == "my_app"
