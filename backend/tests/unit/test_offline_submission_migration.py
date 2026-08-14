"""Round 8-4A — offline submission (migration 0041). Source inspection (the
local backend/alembic package shadows the installed alembic, so the module
can't be imported standalone — same approach as the other migration tests)."""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_20_0000-0041_offline_submission.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0041_offline_submission"
    assert down == "0040_retire_inspection_codes"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_adds_two_nullable_columns() -> None:
    up = _upgrade()
    assert "ADD COLUMN client_submission_id UUID" in up
    assert "ADD COLUMN captured_at TIMESTAMPTZ" in up
    # Nullable — neither ADD COLUMN carries a NOT NULL, and there's no backfill.
    # (The only "NOT NULL" in the file is the index's `IS NOT NULL` predicate.)
    assert "UUID NOT NULL" not in up
    assert "TIMESTAMPTZ NOT NULL" not in up
    assert "UPDATE" not in up
    assert "INSERT INTO" not in up


def test_partial_unique_index_excludes_nulls() -> None:
    up = _upgrade()
    assert "CREATE UNIQUE INDEX uq_records_client_submission_id" in up
    assert "ON records (client_submission_id)" in up
    assert "WHERE client_submission_id IS NOT NULL" in up


def test_upgrade_touches_only_records_and_no_rls_or_grant() -> None:
    up = _upgrade()
    for token in (
        "ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY",
        "CREATE POLICY", "DROP POLICY", "ALTER POLICY",
        "GRANT ", "REVOKE ",
    ):
        assert token not in up, f"unexpected RLS/grant statement: {token}"
    for other in ("plots", "plot_cycles", "plot_access_phones", "suppliers"):
        assert other not in up, f"migration should not touch {other!r}"


def test_does_not_touch_created_at() -> None:
    # created_at semantics must be untouched — the migration never mentions it.
    assert "created_at" not in _upgrade()


def test_downgrade_drops_index_then_both_columns() -> None:
    down = _downgrade()
    assert "DROP INDEX IF EXISTS uq_records_client_submission_id" in down
    assert "DROP COLUMN IF EXISTS captured_at" in down
    assert "DROP COLUMN IF EXISTS client_submission_id" in down
    # Index dropped before the columns it covers.
    assert down.index("DROP INDEX") < down.index("DROP COLUMN IF EXISTS client_submission_id")


def test_model_metadata_matches_schema_after_upgrade() -> None:
    """The Record model must declare exactly what this migration leaves behind:
    two nullable columns and the partial-unique index with the same name."""
    from app.db.models.record import Record

    cols = Record.__table__.c
    assert "client_submission_id" in cols
    assert "captured_at" in cols
    assert cols.client_submission_id.nullable is True
    assert cols.captured_at.nullable is True

    idx = next(
        (i for i in Record.__table__.indexes if i.name == "uq_records_client_submission_id"),
        None,
    )
    assert idx is not None, "model is missing uq_records_client_submission_id"
    assert idx.unique is True
    assert [c.name for c in idx.columns] == ["client_submission_id"]
