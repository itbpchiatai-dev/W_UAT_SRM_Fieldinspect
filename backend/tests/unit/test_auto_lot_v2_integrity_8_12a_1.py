"""Round 8-12A.1 — Auto Lot V2 contract and database hardening:

  * migration 0049 (strengthened CHECK, explicit index predicates, V2 lot_no
    uniqueness)
  * collision-safe series-key encoding
  * the "blank lotNo must produce an Auto Lot or a clean error" contract
  * IntegrityError → 409 mapping for BOTH V2 unique indexes

Source inspection for the migration (backend/alembic shadows the installed
alembic package), model-metadata agreement, and DB-less behaviour tests
elsewhere.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.db.models.plot_cycle import PlotCycle
from app.repositories import plot_cycle_repository as repo
from app.services.lot_number import (
    AutoLotMissingComponentError,
    build_auto_lot_series_key,
    format_auto_lot_no,
)

_MOD = "app.repositories.plot_cycle_repository"

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_08_05_0100-0049_auto_lot_v2_integrity.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


# ===========================================================================
# Part D — migration 0049
# ===========================================================================

def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0049_auto_lot_v2_integrity"
    assert down == "0048_supplier_lot_auto_lot_v2"
    assert len(revision) <= 32


def test_check_keeps_the_v1_branch_exactly_as_legacy_rows_need_it() -> None:
    """Every existing auto row on dev is V1 (series key NULL) with a po_number.
    That branch must stay intact or 32 correct rows would become invalid."""
    up = _upgrade()
    assert "auto_lot_series_key IS NULL AND po_number IS NOT NULL" in up


def test_check_requires_v2_rows_to_carry_their_own_components() -> None:
    """A V2 lot number is RENDERED from cycle_label + p_code; storing the lot
    without them would leave a value nobody can re-derive or explain."""
    up = _upgrade()
    assert "auto_lot_series_key IS NOT NULL" in up
    assert "cycle_label IS NOT NULL AND btrim(cycle_label) <> ''" in up
    assert "p_code IS NOT NULL AND btrim(p_code) <> ''" in up


def test_check_rejects_blank_not_just_null_components() -> None:
    """'' and '   ' are exactly what normalize_cycle_label/normalize_p_code
    refuse to produce, so the DB must not accept them either."""
    up = _upgrade()
    assert up.count("btrim(") >= 2


def test_v2_running_index_predicate_names_auto_explicitly() -> None:
    up = _upgrade()
    assert "uq_plot_cycles_auto_lot_series_running" in up
    assert "WHERE lot_no_source = 'auto' AND auto_lot_series_key IS NOT NULL" in up


def test_v2_lot_no_unique_index_is_added_and_scoped_to_v2_rows() -> None:
    """Different series can render the SAME lot_no text (every component may
    contain '-'), so uniqueness of the running number is not enough."""
    up = _upgrade()
    assert "uq_plot_cycles_auto_lot_v2_lot_no" in up
    assert "ON plot_cycles (lot_no)" in up


def test_v2_lot_no_index_is_not_global_because_legacy_holds_a_duplicate() -> None:
    """A global UNIQUE(lot_no) would fail on existing dev data (one manual +
    one legacy row share a lot_no), so the index must be partial."""
    up = _upgrade()
    lot_no_index = up[up.index("uq_plot_cycles_auto_lot_v2_lot_no"):]
    assert "WHERE lot_no_source = 'auto' AND auto_lot_series_key IS NOT NULL" in lot_no_index


def test_migration_changes_no_application_data() -> None:
    for sql in (_upgrade(), _downgrade()):
        upper = sql.upper()
        assert "UPDATE PLOT_CYCLES" not in upper
        assert "INSERT" not in upper
        assert "DELETE" not in upper
        assert "TRUNCATE" not in upper
        assert "DROP TABLE" not in upper
        assert "DROP COLUMN" not in upper      # 0049 is constraints/indexes only


def test_downgrade_restores_0048_definitions() -> None:
    down = _downgrade()
    assert "DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_v2_lot_no" in down
    # 0048's running-index predicate (no explicit 'auto' term)
    assert "WHERE auto_lot_series_key IS NOT NULL;" in down
    # 0048's looser CHECK
    assert "auto_lot_series_key IS NOT NULL OR po_number IS NOT NULL" in down


def test_model_mirrors_the_0049_check() -> None:
    checks = [
        str(c.sqltext) for c in PlotCycle.__table__.constraints
        if getattr(c, "name", None) == "ck_plot_cycles_auto_lot_requires_fields"
    ]
    assert len(checks) == 1
    sql = checks[0]
    assert "auto_lot_series_key IS NULL AND po_number IS NOT NULL" in sql
    assert "btrim(cycle_label)" in sql and "btrim(p_code)" in sql


def test_model_declares_the_v2_lot_no_index_with_the_same_predicate() -> None:
    by_name = {ix.name: ix for ix in PlotCycle.__table__.indexes}
    ix = by_name["uq_plot_cycles_auto_lot_v2_lot_no"]
    assert ix.unique
    assert [c.name for c in ix.columns] == ["lot_no"]
    where = str(ix.dialect_options["postgresql"]["where"])
    assert "lot_no_source = 'auto'" in where
    assert "auto_lot_series_key IS NOT NULL" in where


def test_model_running_index_predicate_matches_0049() -> None:
    by_name = {ix.name: ix for ix in PlotCycle.__table__.indexes}
    where = str(by_name["uq_plot_cycles_auto_lot_series_running"]
                .dialect_options["postgresql"]["where"])
    assert "lot_no_source = 'auto'" in where
    assert "auto_lot_series_key IS NOT NULL" in where


# ===========================================================================
# Part C — collision-safe series key
# ===========================================================================

def test_series_key_is_length_prefixed_not_delimiter_joined() -> None:
    key = build_auto_lot_series_key("SUP010", "2605", "WM-141")
    assert key.startswith("v2|")
    assert "6:SUP010" in key and "4:2605" in key and "6:WM-141" in key


@pytest.mark.parametrize(
    "a,b",
    [
        # the dash case from the round brief
        (("S", "26", "may-1"), ("S", "26-may", "1")),
        # a component containing the OLD U+001F delimiter — 8-12A's join was
        # forgeable by simply pasting one
        (("S", "26\x1fmay", "1"), ("S", "26", "may\x1f1")),
        # the new "|" and ":" separators are equally forgeable by a delimiter
        # join, and equally harmless to a length-prefixed one
        (("S", "1|2", "3"), ("S", "1", "2|3")),
        (("S", "1:2", "3"), ("S", "1", "2:3")),
        # a digit-prefixed value that could imitate a length prefix
        (("S", "3:abc", "x"), ("S", "3", ":abcx")),
    ],
)
def test_distinct_triples_never_collide(a, b) -> None:
    assert build_auto_lot_series_key(*a) != build_auto_lot_series_key(*b)


def test_unicode_components_are_supported_and_distinct() -> None:
    thai = build_auto_lot_series_key("SUP010", "รอบทดลอง", "WM-141")
    other = build_auto_lot_series_key("SUP010", "รอบทดลอง2", "WM-141")
    assert thai != other
    assert "รอบทดลอง" in thai


def test_series_key_is_deterministic_across_calls() -> None:
    """It is stored in a DB index — a value that varied per process (e.g.
    Python's hash()) would silently restart a series' running sequence."""
    args = ("SUP010", "2605", "WM-141")
    assert build_auto_lot_series_key(*args) == build_auto_lot_series_key(*args)


def test_series_key_does_not_use_python_hash() -> None:
    """Checks the CODE, not the docstring (which legitimately explains why
    hash() is unusable here)."""
    src = inspect.getsource(build_auto_lot_series_key)
    body = src[src.index('"""', src.index('"""') + 3) + 3:]   # after the docstring
    assert "hash(" not in body


@pytest.mark.parametrize(
    "changed",
    [("SUP011", "2605", "WM-141"), ("SUP010", "26-may", "WM-141"),
     ("SUP010", "2605", "WM-142")],
)
def test_changing_any_component_changes_the_key(changed) -> None:
    base = build_auto_lot_series_key("SUP010", "2605", "WM-141")
    assert build_auto_lot_series_key(*changed) != base


def test_series_key_fits_the_column_for_realistic_inputs() -> None:
    """The key is VARCHAR(255); the lot_no length guard (100) fires first for
    anything that would overflow it, but check a realistic worst case."""
    key = build_auto_lot_series_key("S" * 30, "L" * 40, "P" * 24)
    assert len(key) <= 255


# ===========================================================================
# Part B — blank lotNo must yield an Auto Lot or a clean error
# ===========================================================================

def _plot():
    return SimpleNamespace(id=uuid4(), plot_code="SUP010-P001", supplier_id=uuid4())


def _mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def test_resolve_lot_fields_has_no_silent_null_branch() -> None:
    """The function must never return a NULL lot for an Auto request — the
    only ways out are a Manual lot, a generated Auto lot, or a raise."""
    src = inspect.getsource(repo._resolve_lot_fields)
    assert "return None, None, None, None" not in src
    assert "raise AutoLotMissingComponentError" in src


def test_resolve_lot_fields_no_longer_takes_an_auto_required_flag() -> None:
    """8-12A gated the rejection behind a flag that create never passed. The
    flag is gone rather than left as a lie about what it controls."""
    assert "auto_required" not in inspect.signature(repo._resolve_lot_fields).parameters


@pytest.mark.parametrize("blank_lot", [None, "", "   "])
async def test_every_blank_lot_form_requests_an_auto_lot(blank_lot) -> None:
    """None, "" and "   " must all mean the same thing — a user cannot land in
    a lotless cycle by typing spaces."""
    db, plot = _mock_db(), _plot()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._supplier_code_for_plot", AsyncMock(return_value="SUP010")), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        with pytest.raises(AutoLotMissingComponentError):
            await repo.create_cycle(db, plot, lot_no=blank_lot, cycle_label=None, p_code="X")
    db.add.assert_not_called()


# ===========================================================================
# Part E — error contract
# ===========================================================================

def test_missing_component_error_never_echoes_a_value() -> None:
    exc = AutoLotMissingComponentError(("cycleLabel",))
    assert "cycleLabel" in str(exc)
    # it carries field NAMES only — there is nowhere for a value to live
    assert exc.missing == ("cycleLabel",)


def test_api_detail_names_the_field_in_thai_without_mentioning_po() -> None:
    from app.api.v1.plots import _auto_lot_missing_detail

    detail = _auto_lot_missing_detail(("cycleLabel",))
    assert "ชื่อรอบปลูก" in detail
    assert "PO" not in detail          # the retired V1 advice must be gone


def test_api_detail_for_an_unresolvable_supplier_is_actionable() -> None:
    """"Fill in supplierCode" would be nonsense — the user cannot type the
    plot's supplier into the cycle form. Say what actually went wrong."""
    from app.api.v1.plots import _auto_lot_missing_detail

    detail = _auto_lot_missing_detail(("supplierCode",))
    assert "ไม่พบ Supplier ของแปลง" in detail


def test_api_detail_lists_every_missing_field() -> None:
    from app.api.v1.plots import _auto_lot_missing_detail

    detail = _auto_lot_missing_detail(("cycleLabel", "pCode"))
    assert "ชื่อรอบปลูก" in detail and "P.Code" in detail


def test_every_create_path_maps_the_error_to_422() -> None:
    """A missing component is a data problem (422), not a server fault (500).
    All five lot-writing endpoints must map it."""
    import app.api.v1.plots as plots_mod

    for fn in (plots_mod.start_plot_cycle, plots_mod.update_plot_cycle,
               plots_mod.rollover_plot_cycle, plots_mod.create_plot_with_cycle,
               plots_mod.reactivate_plot_with_cycle):
        src = inspect.getsource(fn)
        assert "AutoLotMissingComponentError" in src, f"{fn.__name__} must map it"
        assert "status_code=422" in src


# ===========================================================================
# Part F — concurrency: both V2 unique indexes surface as 409
# ===========================================================================

def test_endpoints_map_integrity_error_to_409_not_500() -> None:
    """Covers BOTH V2 backstops — the running-number index and the new lot_no
    index raise the same IntegrityError, and neither may become a 500."""
    import app.api.v1.plots as plots_mod

    for fn in (plots_mod.start_plot_cycle, plots_mod.update_plot_cycle,
               plots_mod.rollover_plot_cycle):
        src = inspect.getsource(fn)
        assert "except IntegrityError" in src, f"{fn.__name__} must catch it"
        assert "status_code=409" in src


async def test_a_losing_racer_gets_409_and_writes_nothing() -> None:
    """Simulates the DB backstop firing: whichever transaction loses the race
    on either V2 index gets a clean conflict, never a duplicate lot."""
    import app.api.v1.plots as plots_mod

    plot = SimpleNamespace(id=uuid4(), plot_code="P001", supplier_id=uuid4(),
                           is_active=True)
    cycle = SimpleNamespace(id=uuid4(), plot_id=plot.id, status="active",
                            cycle_label="2605", p_code="WM-141", lot_no="L",
                            lot_no_source="auto", lot_running_no=1,
                            auto_lot_series_key="k", po_number=None,
                            supplier_lot_no=None, crop=None, variety=None)
    boom = IntegrityError("stmt", {}, Exception("uq_plot_cycles_auto_lot_v2_lot_no"))

    db = MagicMock()
    db.flush = AsyncMock()
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch("app.api.v1.plots.plot_cycle_repo.get_cycle_for_plot",
               AsyncMock(return_value=cycle)), \
         patch("app.api.v1.plots.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=cycle)), \
         patch("app.api.v1.plots.plot_cycle_repo.update_cycle",
               AsyncMock(side_effect=boom)), \
         patch("app.api.v1.plots.plot_cycle_repo.sync_plot_mirror_from_cycle",
               AsyncMock()) as mk_sync:
        from app.schemas.plot import PlotCycleUpdate
        with pytest.raises(HTTPException) as exc:
            await plots_mod.update_plot_cycle(
                plot_id=plot.id, cycle_id=cycle.id,
                payload=PlotCycleUpdate(lotNo=None), db=db,
            )
    assert exc.value.status_code == 409
    mk_sync.assert_not_awaited()      # nothing downstream ran


def test_no_advisory_lock_was_introduced() -> None:
    """Part F — the DB index is the backstop; an advisory lock would add
    deadlock risk across the multi-series Excel transaction for no proven
    benefit."""
    src = inspect.getsource(repo)
    assert "pg_advisory" not in src.lower()


# ===========================================================================
# The formula itself is unchanged by this round
# ===========================================================================

def test_the_v2_formula_still_renders_the_contract_example() -> None:
    assert format_auto_lot_no(
        cycle_label="2605", supplier_code="SUP010", p_code="WM-141", running=3,
    ) == "2605-SUP010-WM-141-003"
