"""Round 5.7 — inspection_protocol_criteria DB constraints.

Source/metadata inspection (no DB fixture in this repo): the migration 0033
adds the CHECK/UNIQUE constraints with a non-mutating preflight, and the ORM
model declares the same constraints so metadata matches the DB.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.db.models.inspection_protocol import InspectionProtocolCriterion

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_12_0000-0033_protocol_constraints.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")

# The two CHECK constraint names (as expanded by app/db/base.py's `ck` naming
# convention) plus the new UNIQUE — model metadata and migration use these.
_CK_SLOT = "ck_inspection_protocol_criteria_slot_allowlist"
_CK_ORDER = "ck_inspection_protocol_criteria_order_range"
_UQ_ORDER = "uq_protocol_stage_order"


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_migration_revision_chain() -> None:
    # The migration can't be imported standalone (the local backend/alembic/
    # package shadows the installed `alembic`, so `from alembic import op`
    # fails), so assert the revision markers from source.
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0033_protocol_constraints"
    assert down == "0032_protocol_criteria"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_upgrade_adds_slot_order_and_unique_constraints() -> None:
    up = _upgrade()

    assert _CK_SLOT in up
    assert "slot IN" in up
    for slot in ("fieldPrepScore", "weatherScore", "careScore", "varietyResistanceScore"):
        assert slot in up

    assert _CK_ORDER in up
    assert "order_index BETWEEN 0 AND 3" in up

    assert _UQ_ORDER in up
    assert "UNIQUE (growth_stage, order_index)" in up


def test_upgrade_preflights_without_mutating() -> None:
    up = _upgrade()
    # A preflight that aborts (RAISE) rather than fixing data.
    assert "RAISE EXCEPTION" in up
    assert "Preflight" in up
    # Never mutates existing rows.
    for mutating in ("UPDATE ", "DELETE ", "INSERT "):
        assert mutating not in up


def test_downgrade_drops_the_three_constraints() -> None:
    down = _downgrade()
    for name in (_UQ_ORDER, _CK_ORDER, _CK_SLOT):
        assert f"DROP CONSTRAINT IF EXISTS {name}" in down


def test_model_declares_the_same_constraints() -> None:
    names = {c.name for c in InspectionProtocolCriterion.__table__.constraints if c.name}
    assert "uq_protocol_stage_slot" in names   # from 0032
    assert _UQ_ORDER in names                  # round 5.7
    assert _CK_SLOT in names
    assert _CK_ORDER in names
