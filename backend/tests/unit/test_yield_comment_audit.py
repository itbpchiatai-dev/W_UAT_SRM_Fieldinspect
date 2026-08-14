"""Round 8-8C Part B — audits app/api/v1/records.py and public_records.py's
SOURCE TEXT for the stale "over 150% raises" comment left over from round
8-8B.1's business-rule change (150 is now a non-blocking frontend warning,
never enforced server-side — see yield_calculation.derive_yield's
MAX_STORABLE_YIELD_PCT). A source-text check (not import/introspection) so a
misleading comment reintroduced later fails this test even though it has no
runtime effect. Same read-the-file-directly approach as the migration text
tests (e.g. test_relax_yield_pct_cap_migration.py).
"""
from __future__ import annotations

from pathlib import Path

_APP_V1 = Path(__file__).resolve().parents[2] / "app" / "api" / "v1"
_RECORDS_PY = (_APP_V1 / "records.py").read_text(encoding="utf-8")
_PUBLIC_RECORDS_PY = (_APP_V1 / "public_records.py").read_text(encoding="utf-8")


def test_records_py_has_no_stale_150_percent_raises_comment() -> None:
    assert ">150% raises" not in _RECORDS_PY
    # The CURRENT, correct threshold is documented in its place.
    assert "9999.9" in _RECORDS_PY


def test_public_records_py_has_no_stale_150_percent_raises_comment() -> None:
    assert ">150% raises" not in _PUBLIC_RECORDS_PY
    assert "9999.9" in _PUBLIC_RECORDS_PY
