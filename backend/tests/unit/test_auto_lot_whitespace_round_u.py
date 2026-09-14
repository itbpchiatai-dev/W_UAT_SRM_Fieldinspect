"""An Auto Lot never contains whitespace, and stripping it never makes two
cycles collide (round U).

The cycle label is free text, so "Aug 2026" used to produce
"Aug 2026-TDS-WM-078-003" — a lot number with a space in the middle of it. The
lot is an identifier that goes onto goods and into other systems; the label is
a display name. So the lot loses every whitespace and zero-width character,
and the stored label keeps what the user typed.

The part that would break if done naively: the running number counts within a
SERIES, and the series key was built from the raw label. Stripping only the
lot text would put "Aug 2026" and "Aug2026" in two series rendering one lot —
both would mint 001 and the second cycle would be refused by the database. So
the key is built from the same compacted components as the text.

Uniqueness itself was already the database's job (migration 0049,
uq_plot_cycles_auto_lot_v2_lot_no); round U also stops the endpoints
reporting that refusal as "Plot already has an active planting cycle".
"""
from __future__ import annotations

import ast
import inspect
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.v1 import plots as plots_module
from app.repositories import plot_cycle_repository as repo
from app.services.lot_number import (
    AutoLotMissingComponentError,
    auto_lot_preview,
    build_auto_lot_series_key,
    format_auto_lot_no,
)

_MOD = "app.repositories.plot_cycle_repository"

# Each of these must vanish from a lot. The first group is what a keyboard or
# an Excel paste produces; the second is invisible text copied from Thai
# documents; the last two are where Python's and JS's \s disagree, listed so
# the frontend preview and this module are held to the same set.
INVISIBLE = [
    " ", "\t", "\n", "\r", "\u00a0", "\u3000", "\u2009",
    "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff",
    "\x1f", "\x85",
]


def _lot(label: str, supplier: str = "SUP010", p_code: str = "WM-141", running: int = 1) -> str:
    return format_auto_lot_no(
        cycle_label=label, supplier_code=supplier, p_code=p_code, running=running,
    )


# --- the lot text --------------------------------------------------------------

def test_the_case_that_started_the_round() -> None:
    assert _lot("Aug 2026", supplier="TDS", p_code="WM-078", running=3) == "Aug2026-TDS-WM-078-003"


@pytest.mark.parametrize("ch", INVISIBLE, ids=lambda c: f"U+{ord(c):04X}")
def test_every_invisible_character_leaves_every_component(ch: str) -> None:
    lot = _lot(f"Aug{ch}2026", supplier=f"SUP{ch}010", p_code=f"WM{ch}-141")
    assert lot == "Aug2026-SUP010-WM-141-001"


def test_nothing_but_letters_digits_and_dashes_survives_a_messy_paste() -> None:
    lot = _lot("\ufeff Aug\u00a0 2026\u200b\t", supplier=" SUP 010 ", p_code="\u200bWM - 141\n")
    assert lot == "Aug2026-SUP010-WM-141-001"
    assert not re.search(r"\s", lot)


def test_a_thai_label_loses_its_spaces_but_keeps_its_letters() -> None:
    assert _lot("รอบ ทดลอง") == "รอบทดลอง-SUP010-WM-141-001"


@pytest.mark.parametrize("label", ["\u200b", "\u200b\u00a0\ufeff", " \u2060 "])
def test_a_component_of_only_invisible_characters_is_missing(label: str) -> None:
    """Otherwise it would pass the blank check and leave an empty segment:
    "-SUP010-WM-141-001"."""
    with pytest.raises(AutoLotMissingComponentError) as info:
        _lot(label)
    assert info.value.missing == ("cycleLabel",)


# --- the series key: the part that would break --------------------------------

def test_spelled_differently_by_whitespace_is_one_series() -> None:
    keys = {
        build_auto_lot_series_key("SUP010", label, "WM-141")
        for label in ("Aug 2026", "Aug2026", "Aug\u00a02026", "Aug\u200b2026",
                      "Aug\t2026", " Aug  2026 ")
    }
    assert len(keys) == 1, (
        "labels that render ONE lot are counted in different series — each would "
        "mint 001 and the database would refuse the second cycle"
    )


def test_whitespace_in_the_supplier_code_or_p_code_is_one_series_too() -> None:
    assert build_auto_lot_series_key("SUP 010", "2605", "WM 141") == \
        build_auto_lot_series_key("SUP010", "2605", "WM141")


def test_a_series_without_whitespace_keys_exactly_as_before() -> None:
    """The reason no scheme bump or key rewrite was needed: every existing
    series whose components had no whitespace — which is nearly all of them —
    keeps its key and so continues its count instead of restarting at 001."""
    assert build_auto_lot_series_key("SUP010", "2605", "WM-141") == "v2|6:SUP010|4:2605|6:WM-141"


def test_a_dash_still_cannot_merge_two_different_series() -> None:
    assert build_auto_lot_series_key("S", "26", "may-1") != \
        build_auto_lot_series_key("S", "26-may", "1")


# --- through create_cycle ------------------------------------------------------

async def _create(cycle_label: str, running: int = 1):
    plot = SimpleNamespace(id=uuid4(), plot_code="P", supplier_id=uuid4())
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    next_running = AsyncMock(return_value=running)
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", next_running), \
         patch(f"{_MOD}._supplier_code_for_plot", AsyncMock(return_value="SUP010")), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(db, plot, cycle_label=cycle_label, p_code="WM-141")
    return cycle, next_running.await_args.args[1]


async def test_the_label_is_stored_as_typed_and_only_the_lot_is_compacted() -> None:
    """The user's decision: the label is a display name, the lot an identifier."""
    cycle, _ = await _create("Aug 2026")
    assert cycle.cycle_label == "Aug 2026"
    assert cycle.lot_no == "Aug2026-SUP010-WM-141-001"


async def test_two_spellings_draw_from_one_running_sequence() -> None:
    """The running number is looked up by the key create_cycle computes — so
    that key, not just the text, must agree across spellings."""
    first, key_a = await _create("Aug 2026", running=1)
    second, key_b = await _create("Aug2026", running=2)
    assert key_a == key_b
    assert (first.lot_no, second.lot_no) == (
        "Aug2026-SUP010-WM-141-001", "Aug2026-SUP010-WM-141-002",
    )


# --- the preview matches what is generated ------------------------------------

@pytest.mark.parametrize(
    "label,supplier,p_code",
    [("Aug 2026", "SUP010", "WM-141"),
     ("\u200bAug\u00a02026 ", " SUP 010", "WM -141"),
     ("รอบ ทดลอง", "SUP010", "WM\t141")],
)
def test_the_excel_preview_shows_the_lot_that_will_be_generated(label, supplier, p_code) -> None:
    preview = auto_lot_preview(label, supplier, p_code)
    generated = format_auto_lot_no(
        cycle_label=label, supplier_code=supplier, p_code=p_code, running=1,
    )
    assert preview.replace("###", "001") == generated


def test_a_preview_of_only_invisible_characters_shows_the_placeholder() -> None:
    assert auto_lot_preview("\u200b ", "SUP010", "WM-141") == "<cycleLabel>-SUP010-WM-141-###"


# --- when the database refuses a lot, say so ---------------------------------

class _PgUniqueViolation(Exception):
    """Stands in for asyncpg's UniqueViolationError, which is what carries
    ``constraint_name`` at runtime (public_records verified the shape)."""

    def __init__(self, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.constraint_name = constraint_name


def _integrity_error(constraint_name: str) -> IntegrityError:
    adapter_error = Exception("adapter")
    adapter_error.__cause__ = _PgUniqueViolation(constraint_name)
    return IntegrityError("INSERT INTO plot_cycles ...", {}, adapter_error)


@pytest.mark.parametrize(
    "index",
    ["uq_plot_cycles_auto_lot_v2_lot_no", "uq_plot_cycles_auto_lot_series_running"],
)
def test_a_lot_index_violation_is_recognised(index: str) -> None:
    assert plots_module._is_auto_lot_conflict(_integrity_error(index))


@pytest.mark.parametrize(
    "index",
    ["uq_plot_cycles_active_per_plot", "uq_plots_supplier_code", "uq_plot_cycles_plot_id_cycle_no"],
)
def test_any_other_violation_keeps_the_endpoints_own_message(index: str) -> None:
    assert not plots_module._is_auto_lot_conflict(_integrity_error(index))


def test_an_error_with_no_constraint_at_all_is_not_a_lot_conflict() -> None:
    assert not plots_module._is_auto_lot_conflict(IntegrityError("x", {}, Exception("y")))


def test_the_lot_message_is_thai_and_says_nothing_was_saved() -> None:
    msg = plots_module._MSG_AUTO_LOT_TAKEN
    assert "Lot" in msg and "ยังไม่ได้บันทึก" in msg
    assert "active planting cycle" not in msg


_CYCLE_CREATING_CALLS = ("create_cycle(", "rollover_cycle(", "reactivate_plot_with_cycle(")


def _code_only(node: ast.AST, src: str) -> str:
    """The function's source minus its `def` line and every comment. Comments
    first: a guard scan that reads prose passes while the code does nothing —
    round T's first super_admin test did exactly that."""
    body = ast.get_source_segment(src, node).split("\n", 1)[1]
    return re.sub(r"#.*", "", body)


def _integrity_handlers_check_for_a_lot_conflict(node: ast.AST, src: str) -> bool:
    handlers = [
        h for h in ast.walk(node)
        if isinstance(h, ast.ExceptHandler)
        and isinstance(h.type, ast.Name) and h.type.id == "IntegrityError"
    ]
    return bool(handlers) and all(
        "_is_auto_lot_conflict(exc)" in re.sub(r"#.*", "", ast.get_source_segment(src, h))
        for h in handlers
    )


def test_every_endpoint_that_mints_a_lot_tells_a_lot_conflict_apart() -> None:
    """The Excel import also creates cycles, but through the import service in
    one transaction, and its 409 already says nothing was saved — so the scan
    covers the endpoints that call the cycle-creating repository functions."""
    src = inspect.getsource(plots_module)
    minting, unguarded = [], []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not any(call in _code_only(node, src) for call in _CYCLE_CREATING_CALLS):
            continue
        minting.append(node.name)
        if not _integrity_handlers_check_for_a_lot_conflict(node, src):
            unguarded.append(node.name)
    # The scan must actually find the four endpoints, or it proves nothing.
    assert set(minting) >= {
        "create_plot_with_cycle", "start_plot_cycle",
        "rollover_plot_cycle", "reactivate_plot_with_cycle",
    }, minting
    assert not unguarded, (
        f"these endpoints create a cycle (and so mint an Auto Lot) but still "
        f"answer a lot collision with a generic message: {unguarded}"
    )
