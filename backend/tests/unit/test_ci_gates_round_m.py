"""CI must be able to fail, and the coverage denominator must stay honest (round M).

Two things went wrong at once here, and both were invisible for the whole life
of the repository.

1. `.github/workflows/ci.yml` ran the backend suite as `pytest -q || true`.
   That was scaffold defaulting — the comment said to drop it "when the suite
   has cases" — but the suite grew to 3,296 cases and the fallback stayed. A
   failing test, a collection error, or the `--cov-fail-under` gate would all
   have reported the job GREEN. Combined with the workflow's `branches:` filter
   never matching this repo's only branch (fixed in round G), backend CI had
   neither run nor been able to fail.

2. `--cov-fail-under=80` was failing at 79.4%, which read as "the app is
   undertested". It was not: `--cov=app` was also measuring ten one-shot
   operator scripts — seeding, mock data, the UAT reset — 1,030 statements at
   35%. Excluding them, served application code sits at 84.1%.

   The fix is an `omit` list, and an omit list is exactly the kind of thing
   that decays into "anything inconvenient to test". So the criterion is
   pinned below instead of merely written in a comment: a module may be
   omitted ONLY if it is a hand-run script (`__main__` entrypoint) that NO
   application module imports.
"""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = BACKEND / "pyproject.toml"


def _ci_text() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def _pytest_step() -> str:
    """The `run:` line of the backend-test job's pytest step, with its comment
    block stripped so a comment mentioning `|| true` can't satisfy the test."""
    text = _ci_text()
    marker = "  backend-test:"
    assert marker in text, "backend-test job is gone from ci.yml"
    job = text[text.index(marker):]
    # the job ends at the next top-level job (two-space indent, name, colon)
    nxt = re.search(r"\n  [a-z][\w-]*:\n", job[len(marker):])
    if nxt:
        job = job[: len(marker) + nxt.start()]
    return "\n".join(ln for ln in job.splitlines() if not ln.strip().startswith("#"))


# --- 1. the test job must be able to fail ------------------------------------

def test_backend_test_job_does_not_swallow_failures() -> None:
    job = _pytest_step()
    assert "pytest" in job, "backend-test job no longer runs pytest"
    for escape in ("|| true", "|| exit 0", "continue-on-error"):
        assert escape not in job, (
            f"backend-test job contains {escape!r} — a failing test, a "
            f"collection error or the coverage gate would report GREEN. This "
            f"job is the only thing standing between a broken commit and UAT."
        )


def test_advisory_steps_use_continue_on_error_not_shell_escapes() -> None:
    """ruff-format and mypy are deliberately non-blocking, but they must still
    RUN and still be visibly red in the Actions UI. `continue-on-error: true`
    does that; `|| true` hides the failure behind a green tick. If someone
    "tidies" the advisory steps into the latter, the signal is gone."""
    text = _ci_text()
    for step in ("ruff format --check .", "mypy app"):
        assert step in text, f"advisory step {step!r} was removed rather than fixed"
        assert f"{step} || true" not in text, (
            f"{step!r} was silenced with `|| true` — use continue-on-error so "
            f"the failure stays visible"
        )


def test_ruff_lint_stays_a_hard_gate() -> None:
    """`ruff check` is not cosmetic here: its first run over this tree found an
    F821 undefined name inside a test helper orphaned by round K — dead code
    pytest never executed, so no test could have caught it."""
    text = _ci_text()
    assert re.search(r"run:\s*ruff check \.\s*$", text, re.M), (
        "the blocking `ruff check .` step is missing from ci.yml"
    )


def test_frontend_tests_actually_run_in_ci() -> None:
    """The frontend job lint/typecheck/built the app but ran none of its 2,000+
    vitest cases until round M."""
    text = _ci_text()
    assert re.search(r"run:\s*npm test\s*$", text, re.M), (
        "ci.yml no longer runs the frontend test suite"
    )


# --- 2. the coverage omit list must stay justified ---------------------------

def _omitted() -> list[str]:
    cfg = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return cfg["tool"]["coverage"]["run"]["omit"]


def test_coverage_gate_is_still_enforced() -> None:
    cfg = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    addopts = " ".join(cfg["tool"]["pytest"]["ini_options"]["addopts"])
    assert "--cov-fail-under=80" in addopts, (
        "the coverage gate was removed instead of being met — omitting the "
        "operator scripts put real application code at 84.1%, so 80 is not a "
        "number this project has to negotiate with"
    )


@pytest.mark.parametrize("rel", _omitted())
def test_every_omitted_module_is_a_hand_run_script(rel: str) -> None:
    path = BACKEND / rel
    assert path.exists(), f"{rel} is omitted from coverage but no longer exists"
    src = path.read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in src, (
        f"{rel} is omitted from coverage but has no __main__ entrypoint — the "
        f"omit list is for hand-run operator scripts, not for code that is "
        f"awkward to test"
    )


@pytest.mark.parametrize("rel", _omitted())
def test_no_application_module_imports_an_omitted_script(rel: str) -> None:
    """The other half of the criterion. A module reachable from an API route
    stays measured no matter how it is written — if one of these ever starts
    being imported by the app, its coverage has to count again."""
    module = rel.removesuffix(".py").replace("/", ".")
    leaf = module.rsplit(".", 1)[-1]
    package = module.rsplit(".", 1)[0] if "." in module else ""
    omitted_paths = {BACKEND / o for o in _omitted()}

    importers: list[str] = []
    for py in (BACKEND / "app").rglob("*.py"):
        if py in omitted_paths:
            continue  # scripts may import each other; only app code matters
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == module for a in node.names):
                    importers.append(str(py.relative_to(BACKEND)))
            elif isinstance(node, ast.ImportFrom) and node.module:
                # `from app.db.seed import X` or `from app.db import seed`
                if node.module == module or (
                    node.module == package and any(a.name == leaf for a in node.names)
                ):
                    importers.append(str(py.relative_to(BACKEND)))

    assert not importers, (
        f"{rel} is omitted from coverage but application code imports it: "
        f"{sorted(set(importers))}. Either stop importing it, or take it out "
        f"of the omit list — it is part of the app now."
    )
