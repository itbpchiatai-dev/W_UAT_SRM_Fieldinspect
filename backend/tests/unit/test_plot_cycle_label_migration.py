"""Migration 0036 — plot_cycles.cycle_label (round 8.0).

Source inspection only — no DB fixture required.
"""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_15_0000-0036_plot_cycle_label.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0036_plot_cycle_label"
    assert down == "0035_plot_cycles_rls"
    assert len(revision) <= 32


def test_upgrade_adds_nullable_varchar100() -> None:
    up = _upgrade()
    assert "ADD COLUMN cycle_label VARCHAR(100)" in up
    # No NOT NULL constraint — column is deliberately nullable (no backfill).
    assert "NOT NULL" not in up


def test_downgrade_drops_column_safely() -> None:
    down = _downgrade()
    assert "DROP COLUMN IF EXISTS cycle_label" in down
