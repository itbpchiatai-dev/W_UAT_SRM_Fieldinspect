"""Remove one-time scaffold tooling + standard-internal files after setup.

Run this once per project after `python -m app.seed` confirms login works.
It deletes two groups:

One-time scaffold tooling (only needed to *generate* the project):
- setup.bat, setup-existing-db.bat, scripts/{setup,init_project,scaffold,check_python,_stdio}.py
- scripts/build-{user-guide,docs,security}-pdf.py  ← build the STANDARD's PDFs
- tests/test_{scaffold_smoke,setup_guards,checks_units}.py
- MIGRATION.md, cleanup-after-setup.bat, scripts/cleanup-after-setup.py (self)

Standard-internal files (history/notes about the STANDARD, not your app):
- PROGRESS.md, CHANGELOG.md            ← the standard's own dev log / changelog
- docs/dev-journal/, docs/proposals/   ← the standard's working notes
- docs/mockups/                        ← the standard's demo UI previews
- docs/handover-2026-06-01.md, docs/bugs-from-test-2026-05-28.md,
  docs/migration-security-v3.0.2.md    ← dated, standard-specific
- docs/*.pdf                           ← generated docs about the standard

What's KEPT (the standard you actually follow + your app):
- backend/, frontend/, docker/
- docs/*.md layer references (backend/auth/database/security/...), docs/human/,
  docs/patterns/, docs/ops/, docs/AupModal.tsx
- scripts/checks/*.py                  ← invoked by pre-commit hooks
- scripts/smoke-prod.{bat,sh}          ← prod-parity local smoke
- .pre-commit-config.yaml, .github/workflows/, README/AGENTS/CLAUDE

Note: the release zip (`git archive`) already excludes the standard-internal
files via `.gitattributes export-ignore`; this script covers the clone / folder-
copy path, where the whole filesystem (incl. gitignored files) comes along.

Safety: refuses to run when the working dir looks like the upstream
`web-app-standard` template (no project.config + template-shaped repo).
That keeps an accidental invocation in the template repo from nuking the source.

Usage:
    python scripts/cleanup-after-setup.py            # interactive
    python scripts/cleanup-after-setup.py --yes      # skip confirmation
    python scripts/cleanup-after-setup.py --dry-run  # preview only
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


# Fixed list — the self-delete entry MUST stay last (see _resolve_targets).
SELF = "scripts/cleanup-after-setup.py"

TARGETS = [
    # --- one-time scaffold tooling ---
    "setup.bat",
    "setup-existing-db.bat",
    "scripts/setup.py",
    "scripts/init_project.py",
    "scripts/scaffold.py",
    "scripts/check_python.py",
    "scripts/_stdio.py",
    "scripts/build-user-guide-pdf.py",
    "scripts/build-docs-pdf.py",
    "scripts/build-security-pdf.py",
    "tests/test_scaffold_smoke.py",
    "tests/test_setup_guards.py",
    "tests/test_checks_units.py",
    "MIGRATION.md",
    "cleanup-after-setup.bat",
    # --- standard-internal files (about the standard, not the generated app) ---
    "PROGRESS.md",
    "CHANGELOG.md",
    "docs/dev-journal",
    "docs/proposals",
    "docs/mockups",
    "docs/handover-2026-06-01.md",
    "docs/bugs-from-test-2026-05-28.md",
    "docs/migration-security-v3.0.2.md",
    # self — deleted last
    SELF,
]


def _resolve_targets(root: Path) -> list[str]:
    """TARGETS plus any generated `docs/*.pdf` carried in via a filesystem copy,
    keeping the self-delete entry last."""
    base = [t for t in TARGETS if t != SELF]
    pdfs = sorted(
        str(p.relative_to(root)).replace("\\", "/") for p in root.glob("docs/*.pdf")
    )
    for pdf in pdfs:
        if pdf not in base:
            base.append(pdf)
    return base + [SELF]


def _looks_like_template_repo(root: Path) -> bool:
    """True when running inside the upstream template (no project.config
    has been generated yet). Refuse to delete in that case."""
    if (root / "project.config").exists():
        return False
    # Heuristic: the template carries scripts/scaffold.py + tests/ but never
    # a project.config until setup.py runs.
    template_markers = [
        root / "scripts" / "scaffold.py",
        root / "tests" / "test_scaffold_smoke.py",
    ]
    return all(p.exists() for p in template_markers)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete one-time scaffold tooling + standard-internal files."
    )
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted; change nothing.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent

    if _looks_like_template_repo(root):
        print("Refusing to run — this looks like the upstream template repo")
        print(f"  (no project.config at {root}).")
        print("  Run this script only inside a scaffolded project after")
        print("  `python scripts/setup.py` finished successfully.")
        return 1

    targets = _resolve_targets(root)
    present, missing = [], []
    for rel in targets:
        p = root / rel
        (present if p.exists() else missing).append(rel)

    if not present:
        print("Nothing to delete — cleanup has already run.")
        return 0

    print("─" * 60)
    print("  Cleanup — one-time tooling + standard-internal files")
    print("─" * 60)
    print()
    print(f"  Root: {root}")
    print()
    print(f"  Will delete {len(present)} items:")
    for rel in present:
        print(f"    - {rel}")
    if missing:
        print()
        print(f"  Already gone ({len(missing)}):")
        for rel in missing:
            print(f"    - {rel}")
    print()

    if args.dry_run:
        print("  --dry-run set; nothing was changed.")
        return 0

    if not args.yes:
        answer = input("  Proceed? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0

    deleted = 0
    for rel in present:
        p = root / rel
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            deleted += 1
        except OSError as e:
            print(f"  ! failed to delete {rel}: {e}")

    # Clean empty scripts/ and tests/ — only if every target inside them is gone.
    for d in (root / "scripts", root / "tests"):
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    print()
    print(f"  Deleted {deleted}/{len(present)} items.")
    print("  Done. You may also delete this readout from your terminal scrollback.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
