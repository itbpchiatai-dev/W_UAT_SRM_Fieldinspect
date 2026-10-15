"""Migration 0060 -- the executive menu lands at the top level when there is
no "farmlog" node to hang it from.

0059 deployed to UAT, granted the permission to all three roles, and created
no menu row: an admin had deleted the "farmlog" node through the Menus page,
and 0059's INSERT ... SELECT ... FROM menu_items WHERE key = 'farmlog'
selected nothing. These tests pin the shape of the fix.

Source inspection, like test_migration_0051_user_auth_version.py: the local
alembic/ package shadows the installed library, so the module cannot be
imported standalone.
"""
from __future__ import annotations

import re
from pathlib import Path

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "2026_10_15_0100-0060_executive_menu_fallback.py"
_PREVIOUS = _VERSIONS / "2026_10_15_0000-0059_executive_dashboard.py"
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def _constant(src: str, name: str) -> str:
    return re.search(rf'^{name} = "([^"]*)"', src, re.MULTILINE).group(1)


def test_revision_chain_follows_0059() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0060_executive_menu_fallback"
    assert down == "0059_executive_dashboard"
    assert len(revision) <= 32, "alembic_version.version_num is VARCHAR(32)"


def test_it_creates_the_same_menu_0059_meant_to() -> None:
    # One entry, described identically -- not a second, slightly different one.
    previous = _PREVIOUS.read_text(encoding="utf-8")
    for name in ("MENU_KEY", "MENU_TH", "MENU_EN", "MENU_ICON", "MENU_PATH",
                 "PERMISSION_KEY"):
        assert _constant(_SRC, name) == _constant(previous, name), name


def test_the_thai_label_survived_the_write() -> None:
    # The first draft of this file went through a shell heredoc that re-encoded
    # Thai into another codepage; the label would have reached UAT as garbage.
    assert "�" not in _SRC
    label = _constant(_SRC, "MENU_TH")
    assert label and all("฀" <= c <= "๿" for c in label)


def test_the_parent_is_a_scalar_subquery_not_a_join() -> None:
    # THE fix. A FROM-clause join on the parent yields zero rows when the
    # parent is gone; a scalar subquery yields NULL, which is "top level".
    up = _upgrade()
    assert "(SELECT id FROM menu_items WHERE key = '{PARENT_KEY}')" in up
    assert "FROM menu_items p" not in up
    assert "WHERE p.key" not in up


def test_the_order_follows_where_it_lands() -> None:
    up = _upgrade()
    assert "CASE WHEN EXISTS (SELECT 1 FROM menu_items WHERE key = '{PARENT_KEY}')" in up
    assert "THEN {ORDER_UNDER_PARENT} ELSE {ORDER_TOP_LEVEL} END" in up
    assert re.search(r"^ORDER_UNDER_PARENT = 5$", _SRC, re.MULTILINE)
    # After Dashboard / Suppliers (10), before the plots entry (20).
    top = int(re.search(r"^ORDER_TOP_LEVEL = (\d+)$", _SRC, re.MULTILINE).group(1))
    assert 10 < top < 20


def test_the_parent_is_farmlog() -> None:
    assert _constant(_SRC, "PARENT_KEY") == "farmlog"


def test_it_never_moves_an_existing_entry() -> None:
    # Where an admin has put the entry since 0059 must win; DO UPDATE would
    # yank it back on every fresh database that runs both migrations.
    up = _upgrade()
    assert "ON CONFLICT (key) DO NOTHING" in up
    assert "DO UPDATE" not in up


def test_upgrade_only_inserts() -> None:
    up = _upgrade().upper()
    for forbidden in ("DELETE ", "UPDATE MENU_ITEMS", "DROP ", "TRUNCATE"):
        assert forbidden not in up, forbidden


def test_downgrade_deletes_nothing() -> None:
    # On a database where 0059 already made the row, 0060 did nothing;
    # deleting here would strip 0059's menu while still at 0059.
    down = _downgrade()
    assert "op.execute" not in down
    assert "pass" in down


def test_it_touches_no_row_level_security_table() -> None:
    up = _upgrade().lower()
    for table in ("plots", "records", "plot_cycles", "plot_access"):
        assert f" {table} " not in up and f" {table}\n" not in up, table
