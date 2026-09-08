"""Verify a pg_dump data file actually CONTAINS the rows it should.

Why this exists — the failure it was written for:

    plots, plot_cycles, records, plot_access_phones and
    plot_access_credentials all have RLS enabled with FORCE, and their
    policies are granted `TO srm_app` only. `DB_USER` in backend/.env is the
    table OWNER, and FORCE means the owner is filtered too — with no policy
    covering that role, every row is denied.

    A dump taken as the owner therefore comes back with the schema intact,
    a plausible file size, exit status 0, and ZERO rows in exactly the five
    tables that hold the field data. Nothing warns you. Restoring it would
    produce an empty system.

So a dump is never trusted on exit status alone. Run this against it and
compare with the live counts before anything destructive happens.

    python verify_pg_dump_rows.py DUMP.sql --expect plots=43 records=16 ...

Exits non-zero when a table is missing from the dump, holds zero rows, or
disagrees with an --expect value. Tables not named in --expect are reported
but never fail the run.
"""
from __future__ import annotations

import argparse
import re
import sys

# The tables whose emptiness means the dump is worthless. Every one of them is
# RLS-protected; a dump taken by a role the policies don't cover loses exactly
# these and keeps everything else, which is what makes the failure so quiet.
RLS_TABLES = (
    "plots",
    "plot_cycles",
    "records",
    "plot_access_phones",
    "plot_access_credentials",
)

COPY_RE = re.compile(r"^COPY public\.([a-z_]+) \(")


def count_rows(path: str) -> dict[str, int]:
    """rows per table in a pg_dump plain-text file.

    A COPY block runs from its `COPY public.<t> (...) FROM stdin;` header to a
    line holding exactly a backslash and a dot. Everything between is one row.
    """
    counts: dict[str, int] = {}
    table: str | None = None
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            m = COPY_RE.match(line)
            if m:
                table, n = m.group(1), 0
                continue
            if table is None:
                continue
            if line == "\\.":
                counts[table] = n
                table = None
            else:
                n += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument(
        "--expect", nargs="*", default=[],
        help="table=count pairs the dump must match exactly",
    )
    args = ap.parse_args()

    expected: dict[str, int] = {}
    for pair in args.expect:
        name, _, value = pair.partition("=")
        expected[name] = int(value)

    counts = count_rows(args.dump)
    problems: list[str] = []

    for table in RLS_TABLES:
        if table not in counts:
            problems.append(f"{table}: no COPY block in the dump at all")
        elif counts[table] == 0:
            problems.append(
                f"{table}: 0 rows — the classic RLS-filtered dump. "
                f"Re-run pg_dump as DB_APP_USER with PGOPTIONS='-c app.scope=all' "
                f"and --enable-row-security."
            )

    for table, want in expected.items():
        got = counts.get(table)
        if got is None:
            problems.append(f"{table}: expected {want} rows, table absent from dump")
        elif got != want:
            problems.append(f"{table}: expected {want} rows, dump has {got}")

    width = max((len(t) for t in counts), default=10)
    for table in sorted(counts):
        mark = ""
        if table in expected:
            mark = "  OK" if counts[table] == expected[table] else "  MISMATCH"
        elif table in RLS_TABLES and counts[table] == 0:
            mark = "  EMPTY"
        print(f"  {table:<{width}}  {counts[table]:>7}{mark}")

    if problems:
        print("\nDUMP REJECTED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print("\nDump verified — every RLS table carries rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
