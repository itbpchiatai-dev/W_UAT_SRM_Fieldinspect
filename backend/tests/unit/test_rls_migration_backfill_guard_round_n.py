"""A migration that back-fills an RLS table must be self-checking (round N).

Alembic connects as the migration role, not `srm_app`. Every RLS table in this
schema carries FORCE ROW LEVEL SECURITY and grants its policies TO srm_app
only, so the migration role matches NO policy: a `SELECT` returns zero rows and
an `UPDATE`/`DELETE` touches zero rows, with **no error**. On an empty dev
database the statement looks fine because there was nothing to change; on UAT,
with real data, it quietly does nothing.

The project has done this six times, in five migrations, and got away with it
every time — not by luck exactly, but because of the phased-safe pattern it
already follows:

    add column nullable  ->  backfill  ->  ALTER ... SET NOT NULL

DDL is not subject to RLS. So if the backfill in the middle touched zero rows,
the constraint at the end sees the NULLs the backfill was supposed to remove
and the migration FAILS, loudly, at deploy time. The constraint is what turns
a silent no-op into a stopped deploy.

So the rule this pins is not "never write DML in a migration" — that would ban
a pattern the codebase uses correctly. It is:

    a DML statement against an RLS table must be followed, in the same
    function, by a constraint that cannot pass if the DML did nothing.

A backfill with no constraint after it is the dangerous shape: nothing would
ever report that UAT's data was left behind. Write that as a script run as
srm_app instead (see docs — and `app/db/backfill_*.py` for the existing ones).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

# Tables with FORCE ROW LEVEL SECURITY whose policies are granted TO srm_app.
RLS_TABLES = ("plots", "plot_cycles", "records", "plot_access_phones",
              "plot_access_credentials")

_T = "|".join(RLS_TABLES)
DML = re.compile(
    rf"\b(?:UPDATE\s+(?:public\.)?(?:{_T})\b"
    rf"|INSERT\s+INTO\s+(?:public\.)?(?:{_T})\b"
    rf"|DELETE\s+FROM\s+(?:public\.)?(?:{_T})\b)",
    re.I | re.S,
)
# Anything that makes the database re-check every row afterwards.
ENFORCING = re.compile(
    r"nullable=False|SET\s+NOT\s+NULL|CHECK\s*\(|create_check_constraint"
    r"|create_unique_constraint|CREATE\s+UNIQUE\s+INDEX",
    re.I,
)


def _functions(src: str) -> list[tuple[str, str]]:
    """Split a migration into (name, body) for its top-level functions."""
    out = []
    marks = [(m.start(), m.group(1)) for m in re.finditer(r"^def (\w+)\(", src, re.M)]
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(src)
        out.append((name, src[pos:end]))
    return out


def _cases() -> list[tuple[str, str, str]]:
    found = []
    for f in sorted(VERSIONS.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for fn_name, body in _functions(src):
            if DML.search(body):
                found.append((f.name, fn_name, body))
    return found


CASES = _cases()


def test_the_scan_still_finds_the_known_backfills() -> None:
    """If this drops to zero the regex has rotted and every test below is
    passing vacuously. Six statements in five migrations as of round N."""
    assert len(CASES) >= 5, (
        f"expected the five known RLS back-filling migrations, found {len(CASES)}"
    )


@pytest.mark.parametrize(
    "filename,fn_name,body",
    CASES,
    ids=[f"{n}::{fn}" for n, fn, _ in CASES],
)
def test_rls_backfill_is_followed_by_an_enforcing_constraint(
    filename: str, fn_name: str, body: str
) -> None:
    last_dml = max(m.end() for m in DML.finditer(body))
    after = body[last_dml:]
    assert ENFORCING.search(after), (
        f"{filename}::{fn_name} back-fills an RLS table and nothing after it "
        f"would notice if the statement touched zero rows. Alembic runs as the "
        f"migration role, which matches no RLS policy, so on UAT this is a "
        f"silent no-op. Either follow it with a constraint that fails when the "
        f"backfill did nothing (SET NOT NULL / CHECK / unique index — DDL is "
        f"not subject to RLS), or move the backfill into a script that runs as "
        f"srm_app."
    )
