"""Plot + cycle Excel import — primaryPhone/additionalPhones columns (round
8-3E). DB-free, same conventions as test_plot_import_service.py: repo lookups
and write helpers are AsyncMock-patched so these exercise validation rules,
create/preserve/replace semantics, and lock-ordering without a database.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.schemas.plot import PlotAccessPhoneConfig
from app.services import plot_import
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    ImportContext,
    ImportHasErrors,
    build_preview,
    commit_import,
)

_M = "app.services.plot_import"


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    data: list[list] = [list(IMPORT_COLUMNS)]
    for r in rows:
        data.append([r.get(c) for c in IMPORT_COLUMNS])
    return build_xlsx([("plots", data)])


def _ctx(**kw) -> ImportContext:
    kw.setdefault("allowed_supplier_id", None)
    kw.setdefault("can_create", True)
    kw.setdefault("can_update", True)
    return ImportContext(**kw)


def _supplier(**kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.get("id", uuid4()), code=kw.get("code", "SUP001"), is_active=kw.get("is_active", True))


def _plot(**kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.get("id", uuid4()), code=kw.get("code", "SUP001"), is_active=kw.get("is_active", True))


def _cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, crop=None, variety=None, cycle_label=None,
        lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        # Round 8-5B — _capture_lot_result reads these off a created cycle.
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        # Round 8-7A — validation captures active.updated_at.
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _create_row(**over) -> dict[str, str]:
    base = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "P101", "plotName": "แปลงใหม่", "province": "เชียงใหม่",
        # cycleLabel + pCode are required whenever lotNo is blank (round
        # 8-12A.1: a blank lot REQUESTS an Auto Lot). These tests are about
        # phone semantics, so the row just needs to be valid for other reasons.
        "cycleLabel": "2605", "poNumber": "PO25001", "pCode": "Melon-A",
    }
    base.update(over)
    return base


def _update_row(**over) -> dict[str, str]:
    base = {"action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P002"}
    base.update(over)
    return base


def _patch_lookups(*, supplier=..., plot=None, active=None):
    sup = _supplier() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
    )


async def _preview(rows, ctx=None, **lookups):
    ctx = ctx or _ctx()
    p_sup, p_plot, p_active = _patch_lookups(**lookups)
    with p_sup, p_plot, p_active:
        return await build_preview(object(), _xlsx(rows), ctx=ctx)


# --- validation (preview) --------------------------------------------------

async def test_both_phone_columns_blank_is_valid_and_config_is_empty() -> None:
    pv = await _preview([_create_row()], plot=None)
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.primary_phone is None
    assert pv.rows[0].payload.additional_phones == []


async def test_primary_and_additional_parse_and_normalize() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="084-555-2162", additionalPhones="085-555-1234, 0866661234")],
        plot=None,
    )
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.primary_phone == "0845552162"
    assert pv.rows[0].payload.additional_phones == ["0855551234", "0866661234"]


async def test_invalid_primary_phone_format_errors() -> None:
    pv = await _preview([_create_row(primaryPhone="12345")], plot=None)
    assert pv.rows[0].status == "error"
    assert "primaryPhone" in pv.rows[0].message


async def test_invalid_additional_phone_format_errors() -> None:
    pv = await _preview([_create_row(primaryPhone="0845552162", additionalPhones="notaphone")], plot=None)
    assert pv.rows[0].status == "error"
    assert "additionalPhones" in pv.rows[0].message


async def test_additional_without_primary_errors() -> None:
    pv = await _preview([_create_row(additionalPhones="0855551234")], plot=None)
    assert pv.rows[0].status == "error"
    assert "primaryPhone" in pv.rows[0].message


async def test_additional_duplicate_within_row_errors() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="0845552162", additionalPhones="0855551234,0855551234")],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "ซ้ำ" in pv.rows[0].message


async def test_primary_duplicated_in_additional_errors() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="0845552162", additionalPhones="0845552162")],
        plot=None,
    )
    assert pv.rows[0].status == "error"


async def test_more_than_ten_additional_errors() -> None:
    many = ",".join(f"08{i:08d}" for i in range(11))
    pv = await _preview([_create_row(primaryPhone="0845552162", additionalPhones=many)], plot=None)
    assert pv.rows[0].status == "error"
    assert "additionalPhones" in pv.rows[0].message


async def test_ten_additional_is_valid() -> None:
    ten = ",".join(f"08{i:08d}" for i in range(10))
    pv = await _preview([_create_row(primaryPhone="0845552162", additionalPhones=ten)], plot=None)
    assert pv.rows[0].status == "valid"
    assert len(pv.rows[0].payload.additional_phones) == 10


async def test_error_message_never_echoes_the_phone_value() -> None:
    pv = await _preview([_create_row(primaryPhone="0812223333xx")], plot=None)
    assert pv.rows[0].status == "error"
    assert "0812223333" not in pv.rows[0].message


async def test_same_phone_on_two_different_plots_is_valid() -> None:
    pv = await _preview(
        [
            _create_row(plotCode="P101", primaryPhone="0845552162"),
            _create_row(plotCode="P102", primaryPhone="0845552162"),
        ],
        plot=None,
    )
    assert pv.valid_rows == 2


async def test_old_file_without_phone_columns_still_previews_fine() -> None:
    """A file built with only the pre-8-3E columns (no primaryPhone/
    additionalPhones keys at all in the row dict) must still preview + parse
    cleanly — the reader maps by header name, so a missing column is simply
    absent from each row's dict, never a KeyError."""
    legacy_columns = [c for c in IMPORT_COLUMNS if c not in ("primaryPhone", "additionalPhones")]
    data = [legacy_columns, [{"action": "create_plot_with_cycle", "supplierCode": "SUP001",
                               "plotCode": "P999", "plotName": "แปลงเก่า",
                               "cycleLabel": "2605",
                               "poNumber": "PO25001", "pCode": "Melon-A"}.get(c) for c in legacy_columns]]
    content = build_xlsx([("plots", data)])
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), content, ctx=_ctx())
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.primary_phone is None
    assert pv.rows[0].payload.additional_phones == []


# --- preview never mutates --------------------------------------------------

async def test_preview_never_calls_phone_repo() -> None:
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", AsyncMock()) as m_replace:
        await build_preview(
            object(),
            _xlsx([_create_row(primaryPhone="0845552162", additionalPhones="0855551234")]),
            ctx=_ctx(),
        )
    m_replace.assert_not_awaited()


# --- commit: create_plot_with_cycle ----------------------------------------

async def test_commit_create_with_primary_and_additional_calls_replace_after_cycle() -> None:
    created_plot = _plot()
    call_order: list[str] = []
    p_sup, p_plot, p_active = _patch_lookups(plot=None)

    async def _create_plot(db, payload):
        call_order.append("create_plot")
        return created_plot

    async def _create_cycle(db, plot, **kw):
        call_order.append("create_cycle")
        return _cycle()

    async def _replace(db, plot, config):
        call_order.append("replace_phones")
        assert plot is created_plot
        assert config.primary_phone == "0845552162"
        assert config.additional_phones == ["0855551234"]

    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", _create_plot), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", _create_cycle), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", _replace):
        await commit_import(
            object(),
            _xlsx([_create_row(primaryPhone="0845552162", additionalPhones="0855551234")]),
            ctx=_ctx(),
        )

    assert call_order == ["create_plot", "create_cycle", "replace_phones"]


async def test_commit_create_with_no_phone_columns_never_calls_replace() -> None:
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=_plot())), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", AsyncMock()) as m_replace:
        await commit_import(object(), _xlsx([_create_row()]), ctx=_ctx())
    m_replace.assert_not_awaited()


# --- commit: existing-plot actions — preserve / replace semantics ----------

async def test_existing_plot_both_blank_preserves_never_calls_replace() -> None:
    plot = _plot()
    active = _cycle()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", AsyncMock()) as m_replace:
        await commit_import(object(), _xlsx([_update_row()]), ctx=_ctx())
    m_replace.assert_not_awaited()


async def test_existing_plot_primary_given_additional_blank_clears_additional() -> None:
    """Providing primaryPhone with additionalPhones blank means the FULL
    desired config is 'this primary, no additional' — replace_plot_access_
    phones full-replaces, so any previously-active additional numbers are
    deactivated as a side effect of the empty additional_phones list."""
    plot = _plot()
    active = _cycle()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    captured: list[PlotAccessPhoneConfig] = []

    async def _replace(db, plot_arg, config):
        captured.append(config)

    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", _replace):
        await commit_import(
            object(), _xlsx([_update_row(primaryPhone="0845552162")]), ctx=_ctx(),
        )

    assert len(captured) == 1
    assert captured[0].primary_phone == "0845552162"
    assert captured[0].additional_phones == []


async def test_existing_plot_primary_and_many_additional_replaces_full_set() -> None:
    plot = _plot()
    active = _cycle()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    captured: list[PlotAccessPhoneConfig] = []

    async def _replace(db, plot_arg, config):
        captured.append(config)

    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", _replace):
        await commit_import(
            object(),
            _xlsx([_update_row(
                primaryPhone="0845552162",
                additionalPhones="0855551234,0866661234,0877771234",
            )]),
            ctx=_ctx(),
        )

    assert captured[0].primary_phone == "0845552162"
    assert captured[0].additional_phones == ["0855551234", "0866661234", "0877771234"]


async def test_existing_plot_uses_the_locked_plot_object_not_a_new_lookup() -> None:
    """Phone replacement must reuse the SAME Plot row _lock_existing_plots
    already locked (Plot before PlotAccessPhone) — never a fresh unlocked
    fetch."""
    locked_plot = _plot()
    active = _cycle()
    p_sup, p_plot, p_active = _patch_lookups(plot=locked_plot, active=active)

    async def _replace(db, plot_arg, config):
        assert plot_arg is locked_plot

    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=locked_plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", _replace):
        await commit_import(
            object(), _xlsx([_update_row(primaryPhone="0845552162")]), ctx=_ctx(),
        )


# --- all-or-nothing / rollback ----------------------------------------------

async def test_file_with_one_error_row_never_calls_replace_for_the_valid_row() -> None:
    rows = [
        _create_row(plotCode="P101", primaryPhone="0845552162"),
        _create_row(action="frobnicate", plotCode="P102"),
    ]
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=_plot())), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", AsyncMock()) as m_replace:
        with pytest.raises(ImportHasErrors):
            await commit_import(object(), _xlsx(rows), ctx=_ctx())
    m_replace.assert_not_awaited()


async def test_phone_replace_failure_propagates_so_the_whole_request_rolls_back() -> None:
    """replace_plot_access_phones failing (e.g. a DB constraint) must
    propagate out of commit_import — the endpoint's single get_db transaction
    is what rolls the whole file back; this file's helpers never swallow it."""
    plot = _plot()
    active = _cycle()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as m_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await commit_import(
                object(), _xlsx([_update_row(primaryPhone="0845552162")]), ctx=_ctx(),
            )
    m_update.assert_awaited_once()  # the cycle edit DID run before the phone step failed


# --- structural lock-order regression ---------------------------------------

def test_apply_phone_config_called_after_cycle_mutation_in_source() -> None:
    """Structural guard (mirrors test_import_locks_plot_before_any_cycle_call_
    in_source's approach): every _apply_phone_config(...) call site in
    _execute_row must appear textually AFTER that branch's cycle-mutation
    call, so PlotAccessPhone is always locked/written after PlotCycle."""
    src = inspect.getsource(plot_import._execute_row)
    # Round 8-6H added a 7th call site (reactivate_plot_with_cycle branch).
    assert src.count("await _apply_phone_config(") == 7
    # Cheap proxy: the FIRST phone-apply call must come after the FIRST
    # cycle-mutation call (create_cycle) — i.e. phone application is never
    # the very first thing this function does.
    first_phone_call = src.index("await _apply_phone_config(")
    assert first_phone_call > src.index("plot_cycle_repo.create_cycle(")


def test_apply_phone_config_never_locks_before_plot_in_source() -> None:
    """_apply_phone_config itself contains no lookup/lock call — it only ever
    receives an ALREADY-locked Plot object from its caller (_execute_row),
    never re-fetches or re-locks the plot on its own."""
    src = inspect.getsource(plot_import._apply_phone_config)
    assert "get_plot_for_update" not in src
    assert "get_plot_by_code" not in src


# --- result workbook ---------------------------------------------------------

def test_report_row_view_and_workbook_include_phone_columns() -> None:
    from app.services.plot_import_report import ALL_COLUMNS, build_plot_import_result_workbook

    assert "primaryPhone" in ALL_COLUMNS
    assert "additionalPhones" in ALL_COLUMNS

    view = {
        "row_number": 3, "action": "create_plot_with_cycle", "status": "valid",
        "message": "", "error_code": None, "result_cycle_no": 1, "resolved_action": None,
        "raw": {
            "action": "create_plot_with_cycle", "supplierCode": "SUP001", "plotCode": "P101",
            "plotName": "แปลงใหม่", "primaryPhone": "0845552162", "additionalPhones": "0855551234",
        },
    }
    content = build_plot_import_result_workbook([view], phase="COMMIT", completed=True)
    assert content[:2] == b"PK"  # a real zip/xlsx was produced


# --- round 8-3E.1: schema validation restored (no model_construct) ---------

def test_apply_phone_config_never_uses_model_construct_in_source() -> None:
    """Structural regression guard (Part B): the production import path must
    build PlotAccessPhoneConfig through its real constructor — model_construct
    skips validation entirely and must never be the way a row's phone columns
    reach the repository write."""
    src = inspect.getsource(plot_import._apply_phone_config)
    assert ".model_construct(" not in src
    assert "PlotAccessPhoneConfig(" in src


async def test_apply_phone_config_still_calls_replace_with_a_valid_config() -> None:
    """The real constructor re-validates already-normalized values — this
    must be a silent no-op for a row that passed _phone_config's own checks,
    and replace_plot_access_phones must still be called exactly as before."""
    plot = _plot()
    active = _cycle()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    captured: list[PlotAccessPhoneConfig] = []

    async def _replace(db, plot_arg, config):
        assert isinstance(config, PlotAccessPhoneConfig)
        captured.append(config)

    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", _replace):
        await commit_import(
            object(),
            _xlsx([_update_row(primaryPhone="0845552162", additionalPhones="0855551234,0866661234")]),
            ctx=_ctx(),
        )

    assert len(captured) == 1
    assert captured[0].primary_phone == "0845552162"
    assert captured[0].additional_phones == ["0855551234", "0866661234"]


# --- round 8-3E.1 Part C: strict additionalPhones comma-segment parsing ----

async def test_double_comma_leaves_an_empty_segment_and_errors() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="0845552162", additionalPhones="0855551234,,0866661234")],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "additionalPhones มีรายการว่างระหว่าง comma" in pv.rows[0].message


async def test_leading_comma_errors() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="0845552162", additionalPhones=",0855551234")],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "additionalPhones มีรายการว่างระหว่าง comma" in pv.rows[0].message


async def test_trailing_comma_errors() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="0845552162", additionalPhones="0855551234,")],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "additionalPhones มีรายการว่างระหว่าง comma" in pv.rows[0].message


async def test_whitespace_only_segment_between_commas_errors() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="0845552162", additionalPhones="0855551234,   ,0866661234")],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "additionalPhones มีรายการว่างระหว่าง comma" in pv.rows[0].message


async def test_empty_segment_error_never_echoes_a_phone_value() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="0845552162", additionalPhones="0855551234,,0866661234")],
        plot=None,
    )
    assert "0855551234" not in pv.rows[0].message
    assert "0866661234" not in pv.rows[0].message


async def test_empty_segment_row_blocks_the_whole_file_commit() -> None:
    rows = [
        _create_row(plotCode="P101", primaryPhone="0845552162", additionalPhones="0855551234,,0866661234"),
        _create_row(plotCode="P102", primaryPhone="0877771234"),
    ]
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=_plot())), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.phone_repo.replace_plot_access_phones", AsyncMock()) as m_replace:
        with pytest.raises(ImportHasErrors):
            await commit_import(object(), _xlsx(rows), ctx=_ctx())
    m_replace.assert_not_awaited()


async def test_whole_cell_blank_additional_still_preserves_no_error() -> None:
    """A genuinely blank additionalPhones cell (no comma at all) must NOT be
    mistaken for an empty-segment error — this is the ordinary 'no additional
    numbers' case, unchanged from round 8-3E."""
    pv = await _preview([_create_row(primaryPhone="0845552162")], plot=None)
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.additional_phones == []


async def test_single_additional_phone_no_comma_still_valid() -> None:
    pv = await _preview(
        [_create_row(primaryPhone="0845552162", additionalPhones="0855551234")],
        plot=None,
    )
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.additional_phones == ["0855551234"]


# --- round 8-3E.1 Part E: template examples are canonical digit strings ----

def test_template_example_phones_are_canonical_digit_strings_no_dashes() -> None:
    from app.api.v1.plots import _template_example_rows

    # Round 8-6A extracted the literal example dicts out of
    # _plot_template_workbook into _template_example_rows (shared with the
    # contextual workbook's "ตัวอย่าง" sheet) — inspect the function that
    # actually owns them now; _plot_template_workbook itself just calls it.
    src = inspect.getsource(_template_example_rows)
    # Every literal phone-shaped example string in this function's source
    # must be dash-free (canonical ^0[689]\d{8}$ per number, comma-joined
    # with no surrounding whitespace for the additional column).
    import re
    phone_field_values = re.findall(r'"(?:primaryPhone|additionalPhones)":\s*"([^"]*)"', src)
    assert phone_field_values, "expected at least one primaryPhone/additionalPhones example"
    for value in phone_field_values:
        for number in value.split(","):
            assert re.fullmatch(r"0[689]\d{8}", number), f"not a canonical digit string: {number!r}"
        assert " " not in value
