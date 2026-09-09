"""deploy-uat.sh must not be able to report a failed migration as a success (round N).

Until round N, the deploy's migration step was this single line:

    sshu "... alembic upgrade head" 2>&1 | grep -E '...' | sed '...' || true

Three things were wrong with it at once. It is a PIPELINE, so `$?` belongs to
sed, not alembic. The trailing `|| true` discarded even that. And nothing
downstream could compensate: the health gate reads the container healthcheck,
which is `/health`, which app/main.py documents as "deliberately touches
nothing" — a static dict, so that a database hiccup can never restart-loop the
app. A container serving NEW code against an UN-MIGRATED schema is therefore
`healthy`, and the script prints "DONE — $SHA is live".

Same shape as the bug rounds I and J removed from the API (a failure reported
as a success), except the component reporting success is also the one that
decides whether to roll back.

These tests drive the real `migrate()` out of the real script with `sshu`
stubbed, so no UAT host is touched. Running the same four cases against the
pre-round-N script (`git show HEAD~:deploy-uat.sh`) had all four PROCEED.

Note this covers the DECISION, not the ssh transport: what alembic reports and
what the function does about it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY = REPO_ROOT / "deploy-uat.sh"

BASH = shutil.which("bash")
requires_bash = pytest.mark.skipif(BASH is None, reason="bash not available")

# Everything above the `main` section — the function definitions only, so
# sourcing the script does not start a deploy.
HARNESS = r"""
set -uo pipefail
REPO="$1"; CASE="$2"
sed -n '1,/^# -* main -*$/p' "$REPO/deploy-uat.sh" | sed '$d' > "$TMP/funcs.sh"
UAT_KEY=/dev/null
source "$TMP/funcs.sh"
WORK="$TMP/work"; mkdir -p "$WORK"
UAT_DIR=/opt/uat_SRM

# Stand in for the host. Only alembic's answers matter to migrate().
sshu() {
  case "$1" in
    *"alembic upgrade head"*)
        if [ "$CASE" = upgrade_fails ]; then
            echo "ERROR [alembic.util.messaging] relation \"foo\" does not exist"
            return 1
        fi
        echo "INFO  [alembic.runtime.migration] Running upgrade 0054 -> 0055_log_partition_maintenance"
        return 0 ;;
    *"alembic current"*)
        case "$CASE" in
          empty_current) echo "" ;;
          behind)        echo "0054_previous" ;;
          *)             echo "0055_log_partition_maintenance (head)" ;;
        esac
        return 0 ;;
    *"alembic heads"*)
        echo "0055_log_partition_maintenance (head)"
        return 0 ;;
    *) return 0 ;;
  esac
}
migrate
"""


def _run(case: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    harness = tmp_path / "harness.sh"
    harness.write_text(HARNESS, encoding="utf-8", newline="\n")
    return subprocess.run(
        [BASH, str(harness), REPO_ROOT.as_posix(), case],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "TMP": tmp_path.as_posix()},
    )


@requires_bash
def test_a_clean_migration_is_allowed_through(tmp_path: Path) -> None:
    r = _run("happy", tmp_path)
    assert r.returncode == 0, f"a good migration was blocked:\n{r.stdout}\n{r.stderr}"
    assert "schema is at head" in r.stdout


@requires_bash
def test_a_failed_migration_aborts_before_the_containers_are_swapped(tmp_path: Path) -> None:
    """The one that mattered. migrate() runs BEFORE swap_and_check, so aborting
    here leaves UAT serving the old code on the old schema — the safe state,
    and why it dies instead of rolling back (there is nothing to roll back)."""
    r = _run("upgrade_fails", tmp_path)
    assert r.returncode != 0, (
        "alembic upgrade head FAILED and the deploy carried on to swap the "
        f"containers anyway:\n{r.stdout}"
    )
    assert "DEPLOY ABORTED" in r.stderr
    assert "NOT swapped" in r.stderr
    # the operator has to be able to see WHY, not just that it stopped
    assert "does not exist" in r.stderr, "alembic's own error was swallowed"


@requires_bash
def test_an_unstamped_database_aborts(tmp_path: Path) -> None:
    """`alembic upgrade head` exits 0 with nothing stamped in some states; a
    zero exit is not proof the schema moved."""
    r = _run("empty_current", tmp_path)
    assert r.returncode != 0, f"empty alembic_version was accepted:\n{r.stdout}"
    assert "no alembic version stamped" in r.stderr


@requires_bash
def test_a_schema_behind_head_aborts(tmp_path: Path) -> None:
    """A partial or branched history also exits 0. The check is what the
    database SAYS it is versus what head is, not the exit code."""
    r = _run("behind", tmp_path)
    assert r.returncode != 0, f"a schema behind head was accepted:\n{r.stdout}"
    assert "0054_previous" in r.stderr and "head is" in r.stderr


def test_the_swallowing_pipeline_has_not_come_back() -> None:
    """Cheap source guard, so this is caught by reading rather than by a
    deploy. `|| true` on the upgrade is the exact line round N removed."""
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("migrate() {"):src.index("alembic_line() {")]
    lines = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
    upgrade = "\n".join(lines)
    assert "alembic upgrade head" in upgrade
    assert "|| true" not in upgrade, (
        "the migration step swallows its exit status again — a failed upgrade "
        "would be swapped live and reported as DONE"
    )


def test_post_deploy_partition_check_is_wired_in_and_never_fatal() -> None:
    """Round H exposed the log-partition runway; nothing read it. The deploy
    reads it now — but a low runway must not roll back a working deploy."""
    src = DEPLOY.read_text(encoding="utf-8")
    assert "post_deploy_checks" in src
    body = src[src.index("post_deploy_checks() {"):src.index("# ---", src.index("post_deploy_checks() {"))]
    assert "/api/v1/health/partitions" in body
    assert "die " not in body, "a warning about next month must not fail this deploy"
