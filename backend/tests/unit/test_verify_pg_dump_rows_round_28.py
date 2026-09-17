"""The backup verifier must tell an EMPTY table from a FILTERED one (round 28).

deploy-uat.sh refuses to deploy unless the dump it just took really contains
the rows the live database holds — the guard that exists because an
RLS-filtered dump looks perfect and restores an empty system
(scripts/verify_pg_dump_rows.py's own docstring).

It read "0 rows in an RLS table" as proof of that failure. After the UAT wipe,
`records` is legitimately empty, so every deploy aborted on a backup that was
in fact perfect. The live counts are passed in as --expect: when they say the
table is empty, an empty dump MATCHES, and only a disagreement is a problem.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "verify_pg_dump_rows.py"
)
_spec = importlib.util.spec_from_file_location("verify_pg_dump_rows", _SCRIPT)
verify = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(verify)


def _dump(tmp_path, tables: dict[str, int]) -> str:
    """A plain-text pg_dump with `n` COPY rows for each table."""
    lines: list[str] = []
    for table, n in tables.items():
        lines.append(f"COPY public.{table} (id, name) FROM stdin;")
        lines.extend(f"{i}\trow{i}" for i in range(n))
        lines.append("\.")
    path = tmp_path / "db_data.sql"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


ALL_RLS = ("plots", "plot_cycles", "records", "plot_access_phones",
           "plot_access_credentials")


def _run(dump_path, expect: dict[str, int]) -> int:
    argv = [dump_path, "--expect", *[f"{k}={v}" for k, v in expect.items()]]
    import sys
    from unittest.mock import patch
    with patch.object(sys, "argv", ["verify", *argv]):
        return verify.main()


def test_an_empty_table_the_live_count_agrees_with_is_accepted(tmp_path) -> None:
    """UAT today: the plots are there, the inspections have not happened yet."""
    counts = {t: 43 for t in ALL_RLS}
    counts["records"] = 0
    assert _run(_dump(tmp_path, counts), counts) == 0


def test_every_table_empty_and_expected_empty_is_still_accepted(tmp_path) -> None:
    """A freshly wiped database is a legitimate thing to back up."""
    counts = {t: 0 for t in ALL_RLS}
    assert _run(_dump(tmp_path, counts), counts) == 0


def test_a_filtered_dump_is_still_rejected(tmp_path) -> None:
    """The failure the guard exists for: live says 43, the dump has none."""
    live = {t: 43 for t in ALL_RLS}
    dumped = {t: 0 for t in ALL_RLS}
    assert _run(_dump(tmp_path, dumped), live) == 1


def test_one_filtered_table_among_full_ones_is_rejected(tmp_path) -> None:
    dumped = {t: 43 for t in ALL_RLS}
    dumped["plots"] = 0
    live = {t: 43 for t in ALL_RLS}
    assert _run(_dump(tmp_path, dumped), live) == 1


def test_a_table_missing_from_the_dump_is_rejected(tmp_path) -> None:
    counts = {t: 43 for t in ALL_RLS if t != "records"}
    assert _run(_dump(tmp_path, counts), {**counts, "records": 0}) == 1


def test_an_empty_rls_table_with_no_live_count_is_still_suspicious(tmp_path) -> None:
    """No --expect for it means nothing vouches for the emptiness — keep the
    original, careful answer rather than assuming the best."""
    counts = {t: 43 for t in ALL_RLS}
    counts["records"] = 0
    expect = {t: v for t, v in counts.items() if t != "records"}
    assert _run(_dump(tmp_path, counts), expect) == 1


def test_a_count_mismatch_is_rejected(tmp_path) -> None:
    dumped = {t: 43 for t in ALL_RLS}
    live = {**dumped, "plot_cycles": 44}
    assert _run(_dump(tmp_path, dumped), live) == 1
