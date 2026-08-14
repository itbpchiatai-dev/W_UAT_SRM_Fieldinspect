"""Round 8-3G — retire inspection codes (migration 0040). Source inspection
(the local backend/alembic package shadows the installed alembic, so the
module can't be imported standalone — same approach as the other migration
tests)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_19_0000-0040_retire_inspection_codes.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0040_retire_inspection_codes"
    assert down == "0039_plot_access_phones"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_submitted_by_code_made_nullable() -> None:
    up = _upgrade()
    assert 'op.alter_column("records", "submitted_by_code", nullable=True)' in up


def test_supplier_inspection_code_dropped() -> None:
    up = _upgrade()
    assert 'op.drop_column("suppliers", "inspection_code")' in up


def test_no_application_row_mutation() -> None:
    up = _upgrade()
    assert "INSERT INTO" not in up
    assert "UPDATE " not in up
    assert "DELETE FROM" not in up
    # Pydantic/SQL-adjacent helper calls that mutate rows must also be absent.
    assert "session.add(" not in up
    assert "session.execute(delete(" not in up


def test_no_rls_policy_grant_or_index_touched() -> None:
    up = _upgrade()
    for token in (
        "ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY",
        "CREATE POLICY", "DROP POLICY", "ALTER POLICY",
        "GRANT ", "REVOKE ",
        "CREATE INDEX", "DROP INDEX",
    ):
        assert token not in up, f"unexpected RLS/grant/index statement: {token}"


def test_no_other_table_touched() -> None:
    up = _upgrade()
    for other in ("plots", "plot_cycles", "plot_access_phones", "qr_key"):
        assert other not in up, f"migration should not touch {other!r}"


def test_downgrade_refuses_before_any_schema_change() -> None:
    down = _downgrade()
    assert "raise RuntimeError(message)" in down
    assert "Cannot restore retired supplier inspection codes; restore from a " in down
    assert "pre-migration backup" in down
    # No op.* schema call anywhere in downgrade — refuses outright, never a
    # partial reconstruction (e.g. re-adding the column with a fabricated
    # default) before raising.
    assert "op.add_column" not in down
    assert "op.drop_column" not in down
    assert "op.alter_column" not in down
    assert "op.execute" not in down


def test_downgrade_never_fabricates_a_placeholder_code() -> None:
    """No op.* call exists in downgrade() at all (asserted above), so there
    is no code path that could set a fabricated default value — this pins
    that invariant directly rather than string-matching the module's own
    explanatory comment (which legitimately names the old default as an
    example of what NOT to do)."""
    down = _downgrade()
    assert "sa.Column(" not in down
    assert "server_default" not in down


def test_model_metadata_matches_schema_after_upgrade() -> None:
    """Record.submitted_by_code and Supplier must agree with what this
    migration leaves behind — nullable on the former, column gone entirely
    on the latter (checked via the model no longer declaring it)."""
    from app.db.models.record import Record
    from app.db.models.supplier import Supplier

    assert Record.__table__.c.submitted_by_code.nullable is True
    assert "inspection_code" not in Supplier.__table__.c


@pytest.mark.parametrize("filename", [
    "2026_07_02_0000-0023_plots_inspection_code.py",
    "2026_07_06_0000-0027_supplier_inspection_code.py",
])
def test_historical_migrations_still_import_inspection_code_helpers(filename: str) -> None:
    """0040 must not delete app/services/inspection_code.py or its helpers —
    a fresh `alembic upgrade head` on an empty DB still replays 0023/0027,
    which import DEFAULT_INSPECTION_CODE/hash_inspection_code from it."""
    src = (Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename).read_text(
        encoding="utf-8"
    )
    assert "from app.services.inspection_code import" in src
