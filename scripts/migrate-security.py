#!/usr/bin/env python3
"""Migrate an existing scaffolded project to the v3.0.2 security baseline.

Idempotent — safe to re-run. Backups every modified file as <name>.bak.<timestamp>
before writing so a failed patch can be rolled back manually.

Fixes applied:
  1. backend/app/core/config.py  — remove `DB_PASSWORD = "devpassword123"` default
  2. frontend/Dockerfile          — switch to nginxinc/nginx-unprivileged (non-root)
  3. frontend/nginx.conf          — add CSP / HSTS / Referrer-Policy headers
  4. docker/docker-compose.yml    — bind DB port to 127.0.0.1 only
  5. docker/docker-compose.yml    — add cpu/memory resource limits to db service
  6. backend/.env                 — warn if literal 'devpassword123' still present
                                    (rotation requires DB restart — see --rotate-password)

Usage:
  # Single project (run from inside the project)
  python migrate-security.py

  # Single project (explicit path)
  python migrate-security.py --project-dir /path/to/my-app

  # All scaffolded projects under a parent folder
  python migrate-security.py --batch /path/to/projects

  # Preview without modifying anything
  python migrate-security.py --dry-run

  # Also rotate dev DB password (will recreate db container — drops volume!)
  python migrate-security.py --rotate-password

Companion: docs/migration-security-v3.0.2.md
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path


# Current target baseline version. Bump when adding fixes that must be
# applied retroactively; --check uses this to flag projects below it.
CURRENT_BASELINE = "v3.0.2"

# Marker file written by both setup.py (source=scaffold) and this script
# (source=migration) so anyone can tell at a glance whether a project is
# on the latest security baseline or needs migration. Lives at project root
# so it's visible in git status + survives migrations.
BASELINE_FILE = ".security-baseline"


def _read_baseline(root: Path) -> dict[str, str]:
    """Parse .security-baseline (KEY=VALUE format). Returns {} if missing."""
    path = root / BASELINE_FILE
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _write_baseline(root: Path, source: str, dry_run: bool = False) -> None:
    """Write/update .security-baseline. `source` = 'scaffold' or 'migration'."""
    path = root / BASELINE_FILE
    content = (
        f"# Security baseline marker — see docs/migration-security-v3.0.2.md\n"
        f"SECURITY_BASELINE={CURRENT_BASELINE}\n"
        f"APPLIED={date.today().isoformat()}\n"
        f"SOURCE={source}\n"
    )
    if not dry_run:
        path.write_text(content, encoding="utf-8")


# Result of one fix on one file.
@dataclass
class FixResult:
    name: str
    changed: bool
    skipped_reason: str = ""
    warning: str = ""


@dataclass
class MigrationReport:
    project: Path
    is_scaffolded: bool = False
    results: list[FixResult] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)

    @property
    def changed_count(self) -> int:
        return sum(1 for r in self.results if r.changed)

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.warning)


# ───────────────────────────────────────────────────────────────
# I/O helpers
# ───────────────────────────────────────────────────────────────


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup(path: Path, dry_run: bool) -> Path:
    """Copy `path` to `path.bak.<timestamp>`. Returns the backup path."""
    bak = path.with_suffix(path.suffix + f".bak.{_stamp()}")
    if not dry_run:
        bak.write_bytes(path.read_bytes())
    return bak


def _write(path: Path, content: str, dry_run: bool) -> None:
    if not dry_run:
        path.write_text(content, encoding="utf-8")


# ───────────────────────────────────────────────────────────────
# Fix 1 — config.py DB_PASSWORD default
# ───────────────────────────────────────────────────────────────


_CONFIG_DB_PW_RE = re.compile(
    r'^(\s*)DB_PASSWORD\s*:\s*str\s*=\s*"devpassword123"\s*$',
    re.MULTILINE,
)


def fix_config_password_default(root: Path, dry_run: bool,
                                report: MigrationReport) -> None:
    """Strip the `DB_PASSWORD = "devpassword123"` default so empty .env fails
    loudly via Pydantic instead of silently using a known-weak password."""
    path = root / "backend" / "app" / "core" / "config.py"
    name = "config.py: remove DB_PASSWORD devpassword default"
    if not path.exists():
        report.results.append(FixResult(name, False, "file not found"))
        return

    src = path.read_text(encoding="utf-8")
    if _CONFIG_DB_PW_RE.search(src):
        new = _CONFIG_DB_PW_RE.sub(r"\1DB_PASSWORD: str", src)
        report.backups.append(_backup(path, dry_run))
        _write(path, new, dry_run)
        report.results.append(FixResult(name, True))
    elif re.search(r'^\s*DB_PASSWORD\s*:\s*str\s*$', src, re.MULTILINE):
        report.results.append(FixResult(name, False, "already migrated"))
    elif "DB_PASSWORD" not in src:
        report.results.append(FixResult(name, False, "DB_PASSWORD not declared"))
    else:
        report.results.append(FixResult(
            name, False,
            "custom DB_PASSWORD default (not the scaffold literal) — leaving alone",
        ))


# ───────────────────────────────────────────────────────────────
# Fix 2 — frontend/Dockerfile nginx-unprivileged
# ───────────────────────────────────────────────────────────────


_NGINX_FROM_RE = re.compile(
    r'^FROM\s+nginx:([\w.\-]+)\s+AS\s+runtime\s*$',
    re.MULTILINE,
)


def fix_frontend_dockerfile(root: Path, dry_run: bool,
                            report: MigrationReport) -> None:
    """Swap `nginx:<ver>` for the unprivileged variant. Preserves the version
    tag — user's own pin survives the rewrite."""
    path = root / "frontend" / "Dockerfile"
    name = "frontend/Dockerfile: nginx → nginx-unprivileged"
    if not path.exists():
        report.results.append(FixResult(name, False, "file not found"))
        return

    src = path.read_text(encoding="utf-8")
    match = _NGINX_FROM_RE.search(src)
    if match:
        version = match.group(1)
        replacement = f"FROM nginxinc/nginx-unprivileged:{version} AS runtime"
        new = _NGINX_FROM_RE.sub(replacement, src)
        report.backups.append(_backup(path, dry_run))
        _write(path, new, dry_run)
        report.results.append(FixResult(name, True))
    elif "nginxinc/nginx-unprivileged" in src:
        report.results.append(FixResult(name, False, "already migrated"))
    elif "nginx" in src.lower():
        report.results.append(FixResult(
            name, False,
            "non-standard nginx FROM line — review manually",
            warning="frontend/Dockerfile uses an unusual nginx base — check it isn't running as root.",
        ))
    else:
        report.results.append(FixResult(name, False, "no nginx FROM line found"))


# ───────────────────────────────────────────────────────────────
# Fix 3 — nginx.conf security headers
# ───────────────────────────────────────────────────────────────


_HEADERS_TO_ADD = [
    ('Referrer-Policy',
     'add_header Referrer-Policy "strict-origin-when-cross-origin" always;'),
    ('Strict-Transport-Security',
     'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;'),
    ('Content-Security-Policy',
     'add_header Content-Security-Policy "default-src \'self\'; script-src \'self\'; '
     'style-src \'self\' \'unsafe-inline\'; img-src \'self\' data:; font-src \'self\' data:; '
     'connect-src \'self\'; frame-ancestors \'none\'; base-uri \'self\'; form-action \'self\'" always;'),
]


def fix_nginx_conf_headers(root: Path, dry_run: bool,
                           report: MigrationReport) -> None:
    """Append missing security headers. Idempotent: only adds headers that
    aren't already present in the file."""
    path = root / "frontend" / "nginx.conf"
    name = "frontend/nginx.conf: add CSP / HSTS / Referrer-Policy"
    if not path.exists():
        report.results.append(FixResult(name, False, "file not found"))
        return

    src = path.read_text(encoding="utf-8")
    missing = [(hdr, line) for hdr, line in _HEADERS_TO_ADD if hdr not in src]
    if not missing:
        report.results.append(FixResult(name, False, "already migrated"))
        return

    # Anchor on existing X-Frame-Options line (always present in the scaffold).
    anchor_re = re.compile(
        r'^(\s*add_header\s+X-Frame-Options[^\n]*\n)',
        re.MULTILINE,
    )
    anchor = anchor_re.search(src)
    if not anchor:
        report.results.append(FixResult(
            name, False, "no anchor (X-Frame-Options line) found",
            warning="frontend/nginx.conf is heavily customised — add CSP/HSTS/Referrer-Policy by hand.",
        ))
        return

    indent = re.match(r'\s*', anchor.group(1)).group(0)
    new_block = "".join(f"{indent}{line}\n" for _, line in missing)
    insert_at = anchor.end()
    new = src[:insert_at] + new_block + src[insert_at:]

    report.backups.append(_backup(path, dry_run))
    _write(path, new, dry_run)
    label = ", ".join(hdr for hdr, _ in missing)
    report.results.append(FixResult(f"{name} ({label})", True))


# ───────────────────────────────────────────────────────────────
# Fix 4 — docker-compose.yml bind port to 127.0.0.1
# ───────────────────────────────────────────────────────────────


_PORT_RE = re.compile(
    r'^(\s*-\s*)"(\d+):5432"\s*$',
    re.MULTILINE,
)


def fix_compose_port_binding(root: Path, dry_run: bool,
                             report: MigrationReport) -> None:
    """Bind the dev DB port to 127.0.0.1 only — local backend still connects,
    LAN hosts no longer can."""
    path = root / "docker" / "docker-compose.yml"
    name = "docker-compose.yml: bind db port to 127.0.0.1"
    if not path.exists():
        report.results.append(FixResult(name, False, "file not found"))
        return

    src = path.read_text(encoding="utf-8")
    match = _PORT_RE.search(src)
    if match:
        host_port = match.group(2)
        replacement = f'{match.group(1)}"127.0.0.1:{host_port}:5432"'
        new = _PORT_RE.sub(replacement, src, count=1)
        report.backups.append(_backup(path, dry_run))
        _write(path, new, dry_run)
        report.results.append(FixResult(name, True))
    elif re.search(r'"127\.0\.0\.1:\d+:5432"', src):
        report.results.append(FixResult(name, False, "already migrated"))
    else:
        report.results.append(FixResult(
            name, False, "no `:5432` port mapping found (custom compose layout?)",
        ))


# ───────────────────────────────────────────────────────────────
# Fix 5 — docker-compose.yml resource limits
# ───────────────────────────────────────────────────────────────


def fix_compose_resource_limits(root: Path, dry_run: bool,
                                report: MigrationReport) -> None:
    """Append a deploy.resources.limits block to the db service so the dev
    container can't eat the whole host on a runaway query."""
    path = root / "docker" / "docker-compose.yml"
    name = "docker-compose.yml: add db resource limits"
    if not path.exists():
        report.results.append(FixResult(name, False, "file not found"))
        return

    src = path.read_text(encoding="utf-8")
    if "resources:" in src and "limits:" in src:
        report.results.append(FixResult(name, False, "already migrated"))
        return

    # Anchor after the db service's healthcheck block. The scaffold always
    # ends the healthcheck with `retries: 5` followed by a blank line, so we
    # insert before the blank line. Custom compose files may differ — bail
    # with a warning rather than guess.
    anchor_re = re.compile(
        r'(    healthcheck:[\s\S]+?retries:\s*\d+\s*\n)',
    )
    anchor = anchor_re.search(src)
    if not anchor:
        report.results.append(FixResult(
            name, False, "no db healthcheck anchor found",
            warning="docker-compose.yml has no recognisable db healthcheck — add resource limits manually.",
        ))
        return

    deploy_block = (
        "    deploy:\n"
        "      resources:\n"
        "        limits:\n"
        "          cpus: '1.0'\n"
        "          memory: 1G\n"
    )
    insert_at = anchor.end()
    new = src[:insert_at] + deploy_block + src[insert_at:]
    report.backups.append(_backup(path, dry_run))
    _write(path, new, dry_run)
    report.results.append(FixResult(name, True))


# ───────────────────────────────────────────────────────────────
# Fix 6 — warn on .env literal devpassword123 (optionally rotate)
# ───────────────────────────────────────────────────────────────


def fix_env_devpassword_warning(root: Path, dry_run: bool,
                                rotate: bool,
                                report: MigrationReport) -> None:
    """Detect the static dev password in .env. Without --rotate-password we
    just warn — actually rotating requires DB ALTER USER which is outside
    this script's scope without taking down the running container."""
    env_path = root / "backend" / ".env"
    compose_path = root / "docker" / "docker-compose.yml"
    name = "backend/.env: rotate devpassword123 literal"

    if not env_path.exists():
        report.results.append(FixResult(name, False, "backend/.env not found"))
        return

    env_src = env_path.read_text(encoding="utf-8")
    if "DB_PASSWORD=devpassword123" not in env_src:
        report.results.append(FixResult(name, False, "already migrated"))
        return

    if not rotate:
        report.results.append(FixResult(
            name, False, "found 'devpassword123' literal — re-run with --rotate-password to fix",
            warning=(
                "backend/.env still uses the well-known 'devpassword123'. "
                "Rotation requires:\n"
                "    1. docker compose down -v   (DROPS DB DATA — back up first)\n"
                "    2. re-run with --rotate-password\n"
                "    3. docker compose up -d\n"
                "    4. alembic upgrade head\n"
                "Or rotate manually: ALTER USER <user> PASSWORD '<new>' + update .env."
            ),
        ))
        return

    new_pw = secrets.token_urlsafe(16)
    report.backups.append(_backup(env_path, dry_run))
    new_env = env_src.replace("DB_PASSWORD=devpassword123", f"DB_PASSWORD={new_pw}", 1)
    _write(env_path, new_env, dry_run)

    if compose_path.exists():
        compose_src = compose_path.read_text(encoding="utf-8")
        if "POSTGRES_PASSWORD: devpassword123" in compose_src:
            report.backups.append(_backup(compose_path, dry_run))
            new_compose = compose_src.replace(
                "POSTGRES_PASSWORD: devpassword123",
                f"POSTGRES_PASSWORD: {new_pw}", 1,
            )
            _write(compose_path, new_compose, dry_run)

    report.results.append(FixResult(
        f"{name} (new password written — recreate db container to apply)", True,
        warning="Run `docker compose down -v && docker compose up -d && alembic upgrade head` to apply.",
    ))


# ───────────────────────────────────────────────────────────────
# Project detection + driver
# ───────────────────────────────────────────────────────────────


def _is_scaffolded_project(path: Path) -> bool:
    """Heuristic: scaffolded projects always have backend/.env + docker/docker-compose.yml."""
    return (
        (path / "backend" / ".env").exists()
        and (path / "docker" / "docker-compose.yml").exists()
    )


def migrate_one(root: Path, dry_run: bool, rotate: bool) -> MigrationReport:
    report = MigrationReport(project=root)
    if not _is_scaffolded_project(root):
        return report
    report.is_scaffolded = True

    fix_config_password_default(root, dry_run, report)
    fix_frontend_dockerfile(root, dry_run, report)
    fix_nginx_conf_headers(root, dry_run, report)
    fix_compose_port_binding(root, dry_run, report)
    fix_compose_resource_limits(root, dry_run, report)
    fix_env_devpassword_warning(root, dry_run, rotate, report)

    # Stamp the project with the current baseline if we either applied a fix
    # OR found nothing left to do (already migrated by previous run). We
    # skip the stamp when the only remaining issue is the .env password
    # literal that needs --rotate-password — the project isn't really
    # up to date yet.
    has_outstanding = any(
        r.skipped_reason.startswith("found 'devpassword123' literal")
        for r in report.results
    )
    if not has_outstanding:
        _write_baseline(root, source="migration", dry_run=dry_run)
    return report


def check_one(root: Path) -> tuple[str, str, int]:
    """Inspect a project without modifying anything.

    Returns (status_label, baseline_info, outstanding_fixes_count).
    Status: 'up-to-date' | 'needs-migration' | 'not-a-scaffold'.
    """
    if not _is_scaffolded_project(root):
        return ("not-a-scaffold", "—", 0)

    marker = _read_baseline(root)
    # Even if a marker exists we still run a fresh detection — a hand-edit
    # that re-introduces an anti-pattern shouldn't stay hidden behind a
    # stale marker. The marker is informational; pattern truth wins.
    report = MigrationReport(project=root)
    fix_config_password_default(root, dry_run=True, report=report)
    fix_frontend_dockerfile(root, dry_run=True, report=report)
    fix_nginx_conf_headers(root, dry_run=True, report=report)
    fix_compose_port_binding(root, dry_run=True, report=report)
    fix_compose_resource_limits(root, dry_run=True, report=report)
    fix_env_devpassword_warning(root, dry_run=True, rotate=False, report=report)

    outstanding = sum(1 for r in report.results if r.changed)
    # The .env password literal counts as outstanding too (it's a skipped
    # warning, not a changed flag — but the project still has the issue).
    outstanding += sum(
        1 for r in report.results
        if r.skipped_reason.startswith("found 'devpassword123' literal")
    )

    if marker:
        baseline = marker.get("SECURITY_BASELINE", "?")
        applied = marker.get("APPLIED", "?")
        source = marker.get("SOURCE", "?")
        info = f"{baseline} ({source}, {applied})"
    else:
        info = "pre-v3.0.2 (no marker)"

    if outstanding == 0 and marker.get("SECURITY_BASELINE") == CURRENT_BASELINE:
        return ("up-to-date", info, 0)
    if outstanding == 0:
        # No issues but no marker — likely a project that pre-dates the
        # marker but happens to have all the patterns right (rare).
        return ("up-to-date", info, 0)
    return ("needs-migration", info, outstanding)


def _print_report(report: MigrationReport, dry_run: bool) -> None:
    if not report.is_scaffolded:
        print(f"  ⚠️  {report.project}: ไม่ใช่ scaffolded project (no backend/.env + docker/docker-compose.yml) — ข้าม")
        return

    print(f"  📁 {report.project}")
    for r in report.results:
        if r.changed:
            mark = "🔄 DRY" if dry_run else "✅"
            print(f"     {mark}  {r.name}")
        else:
            print(f"     ⏭️   {r.name}  ({r.skipped_reason})")
        if r.warning:
            for line in r.warning.split("\n"):
                print(f"        ⚠️  {line}")

    if report.backups and not dry_run:
        print(f"     💾  Backups: {len(report.backups)} ไฟล์ .bak.{_stamp()}*")
    print()


def _find_projects(parent: Path) -> list[Path]:
    """Find all scaffolded projects directly under parent (depth 1)."""
    if not parent.exists() or not parent.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(parent.iterdir()):
        if child.is_dir() and _is_scaffolded_project(child):
            out.append(child)
    return out


def _run_check(projects: list[Path]) -> int:
    """Report status for each project without modifying anything."""
    print(f"── Security baseline check  (target: {CURRENT_BASELINE}) ─────")
    print()

    up_to_date = 0
    needs_migration = 0
    not_scaffold = 0

    for proj in projects:
        status, info, outstanding = check_one(proj)
        # Truncate long paths for readable column alignment.
        label = str(proj)
        if len(label) > 50:
            label = "…" + label[-49:]
        if status == "up-to-date":
            up_to_date += 1
            print(f"  📁 {label:<50}  ✅ UP TO DATE        {info}")
        elif status == "needs-migration":
            needs_migration += 1
            print(f"  📁 {label:<50}  🔄 NEEDS MIGRATION   {info}  ({outstanding} fix{'es' if outstanding > 1 else ''})")
        else:
            not_scaffold += 1
            print(f"  📁 {label:<50}  ⏭️  NOT A SCAFFOLD")

    print()
    print("─" * 60)
    print(f"  Summary: {up_to_date} up-to-date | {needs_migration} need migration | {not_scaffold} not a scaffold")
    if needs_migration:
        print()
        print("  ℹ️  รัน migrate-security.py โดยไม่มี --check เพื่อ apply fixes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate-security.py",
        description=f"Migrate scaffolded project(s) to {CURRENT_BASELINE} security baseline.",
    )
    parser.add_argument(
        "--project-dir", type=Path, default=None,
        help="Single project to migrate (default: current directory)",
    )
    parser.add_argument(
        "--batch", type=Path, default=None,
        help="Parent folder — migrate every scaffolded sub-folder",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without writing anything",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Report which projects need migration (no changes, no .bak files)",
    )
    parser.add_argument(
        "--rotate-password", action="store_true",
        help="Also rotate dev DB password (requires `docker compose down -v` after)",
    )
    args = parser.parse_args(argv)

    if args.project_dir and args.batch:
        print("ERROR: ใช้ --project-dir หรือ --batch อย่างใดอย่างหนึ่ง ไม่ใช่ทั้งคู่",
              file=sys.stderr)
        return 2
    if args.check and (args.dry_run or args.rotate_password):
        print("ERROR: --check ใช้คู่กับ --dry-run / --rotate-password ไม่ได้ (มันคือ read-only mode อยู่แล้ว)",
              file=sys.stderr)
        return 2

    if args.batch:
        projects = _find_projects(args.batch.resolve())
        if not projects:
            # In --check mode we still want to scan even if no scaffolds —
            # surfaces empty-folder case clearly instead of generic error.
            if args.check:
                projects = sorted(p for p in args.batch.resolve().iterdir() if p.is_dir())
                if not projects:
                    print(f"ไม่พบ folder ใน {args.batch}")
                    return 1
            else:
                print(f"ไม่พบ scaffolded projects ใน {args.batch}")
                return 1
        else:
            print(f"พบ {len(projects)} scaffolded project(s) ใน {args.batch}")
            print()
    else:
        target = (args.project_dir or Path.cwd()).resolve()
        projects = [target]

    if args.check:
        return _run_check(projects)

    mode = "DRY RUN" if args.dry_run else "APPLY"
    rotate_note = " + rotate password" if args.rotate_password else ""
    print(f"── Migrate security {CURRENT_BASELINE}  [{mode}{rotate_note}] ─────")
    print()

    total_changes = 0
    total_warnings = 0
    skipped = 0
    for proj in projects:
        report = migrate_one(proj, args.dry_run, args.rotate_password)
        _print_report(report, args.dry_run)
        if not report.is_scaffolded:
            skipped += 1
            continue
        total_changes += report.changed_count
        total_warnings += report.warning_count

    print("─" * 60)
    print(f"  Projects checked : {len(projects)}")
    print(f"  Skipped (not a scaffold): {skipped}")
    print(f"  Changes {'(would apply)' if args.dry_run else 'applied'}: {total_changes}")
    if total_warnings:
        print(f"  Warnings: {total_warnings}  ← review the messages above")
    print()
    if args.dry_run and total_changes:
        print("  ℹ️  Re-run without --dry-run เพื่อ apply จริง")
    elif total_changes:
        print("  ℹ️  หลัง migrate: รัน `docker compose down && docker compose up -d` ใน docker/ เพื่อให้ port-binding + resource limits มีผล")
        print("       และ rebuild frontend image ถ้าใช้ Docker production stack")
    return 0


if __name__ == "__main__":
    sys.exit(main())
