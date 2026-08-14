"""Round 8-9B.1 — plot inspection password via Plot Import Excel.

Covers the whole path: column semantics, shared-policy validation, the safe
preview payload, the preview-state binding, the commit transaction (hash off
the event loop, no lock held while hashing, all-or-nothing rollback), the
missing-pepper guard, activity logging, and — the one that matters most — that
no plaintext ever leaves the server in a response, a preview_state, a log, or
the result workbook.

DB-less: the repos the importer calls are patched, exactly like
test_plot_import_service.py. Every PIN here is a test-only fixture.
"""
from __future__ import annotations

import datetime
import inspect
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zipfile import ZipFile

import pytest
from pydantic import SecretStr

import app.services.plot_import as plot_import
import app.services.plot_import_report as plot_import_report
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    ImportContext,
    ImportFileError,
    ImportPreviewStateConflict,
    build_preview,
    commit_import,
)

_M = "app.services.plot_import"

# Test-only PINs and pepper — never real credentials, never printed.
PIN = "135790"
PIN_SHORT = "1357"
PIN_LONG = "1" * 20
PEPPER = SecretStr("t" * 40)


def _ctx(**kw) -> ImportContext:
    base = dict(allowed_supplier_id=None, can_create=True, can_update=True, user_id=uuid4())
    base.update(kw)
    return ImportContext(**base)


def _supplier(**kw) -> SimpleNamespace:
    base = dict(id=uuid4(), code="SUP001", name="ซัพพลายเออร์", is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _plot(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), plot_code="P001", name="แปลงหนึ่ง", is_active=True,
        supplier_id=uuid4(),
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, cycle_label="jun2026", status="active",
        crop=None, variety=None, lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    headers = list(plot_import.IMPORT_COLUMNS)
    data = [headers] + [[r.get(h) for h in headers] for r in rows]
    return build_xlsx([("plots", data)])


def _create_row(**over) -> dict[str, str]:
    base = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "P101", "plotName": "แปลงใหม่", "province": "เชียงใหม่",
        "poNumber": "PO25001", "pCode": "Melon-A", "lotNo": "LOT-01",
        # Round 8-17A.1 — required on every new-cycle action now.
        "cycleLabel": "jun2026",
    }
    base.update(over)
    return base


def _update_row(**over) -> dict[str, str]:
    base = {
        "action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P001",
        # Round 8-17A.1 — update_current_cycle replaces cycle_label in full
        # (no preserve-on-blank carve-out), and _cycle() below defaults to a
        # non-blank cycle_label="jun2026" — a blank cell here would try to
        # CLEAR it, which the endpoint now refuses. Repeat the same label by
        # default so these credential-focused tests keep exercising what
        # they were written to test.
        "cycleLabel": "jun2026",
    }
    base.update(over)
    return base


def _patch_lookups(*, supplier=..., plot=None, active=None):
    sup = _supplier() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
    )


def _patch_credential_status(mapping=None):
    return patch(
        f"{_M}.credential_repo.get_credential_status_for_plots",
        AsyncMock(return_value=mapping or {}),
    )


async def _preview(rows, *, ctx=None, status=None, **lookups):
    p_sup, p_plot, p_active = _patch_lookups(**lookups)
    with p_sup, p_plot, p_active, _patch_credential_status(status):
        return await build_preview(object(), _xlsx(rows), ctx=ctx or _ctx())


# --- columns --------------------------------------------------------------

def test_both_columns_exist_and_are_documented() -> None:
    assert "inspectionPasswordStatus" in plot_import.IMPORT_COLUMNS
    assert "newInspectionPassword" in plot_import.IMPORT_COLUMNS
    desc = plot_import.TEMPLATE_COLUMN_DESCRIPTIONS
    assert desc["inspectionPasswordStatus"] == "สถานะรหัสยืนยันแปลงปัจจุบัน ใช้ดูข้อมูลเท่านั้น"
    assert desc["newInspectionPassword"] == (
        "กรอกตัวเลข 4 ถึง 20 หลักเมื่อต้องการตั้งหรือเปลี่ยนรหัส เว้นว่างเพื่อคงรหัสเดิม"
    )


def test_status_column_is_never_read_by_the_parser() -> None:
    """Informational only: a user editing it must not change what commits."""
    src = inspect.getsource(plot_import._parse_row)
    assert "inspectionPasswordStatus" not in src
    src_all = inspect.getsource(plot_import)
    # The status constant is only ever WRITTEN (template) — the parser body
    # never reads the column out of `raw`.
    assert 'raw.get("inspectionPasswordStatus")' not in src_all
    assert '_str(raw, "inspectionPasswordStatus")' not in src_all


async def test_editing_the_status_cell_changes_nothing() -> None:
    plot = _plot()
    preview = await _preview(
        [_update_row(inspectionPasswordStatus="configured")],
        plot=plot, active=_cycle(),
    )
    row = preview.rows[0]
    assert row.status == "valid"
    assert row.inspection_password_change is None   # no password → no change


# --- validation (shared policy) -------------------------------------------

@pytest.mark.parametrize("pin", [PIN_SHORT, "0000", "1111", "1234", "987654", PIN, PIN_LONG])
async def test_accepts_every_legal_code_including_repeated_and_sequential(pin: str) -> None:
    preview = await _preview(
        [_update_row(newInspectionPassword=pin)],
        plot=_plot(), active=_cycle(), status={},
    )
    assert preview.rows[0].status == "valid", preview.rows[0].message


@pytest.mark.parametrize(
    "bad", ["123", "1" * 21, "13579a", "12 34", "12-34", "๑๓๕๗", "１２３４"],
)
async def test_rejects_malformed_codes_with_the_shared_static_message(bad: str) -> None:
    preview = await _preview(
        [_update_row(newInspectionPassword=bad)], plot=_plot(), active=_cycle(),
    )
    row = preview.rows[0]
    assert row.status == "error"
    assert "newInspectionPassword" in row.message
    assert "รหัสยืนยันแปลงต้องเป็นตัวเลข 0-9 จำนวน 4 ถึง 20 หลัก" in row.message
    assert bad not in row.message          # never echoes the submitted value


async def test_row_error_names_the_row_number_via_the_result() -> None:
    preview = await _preview(
        [_update_row(newInspectionPassword="123")], plot=_plot(), active=_cycle(),
    )
    assert preview.rows[0].row_number == 2   # header row 1, data row 2
    assert preview.rows[0].status == "error"


def test_policy_is_not_reimplemented_in_the_importer() -> None:
    """The 4-20 rule must live in ONE place — a second copy here would drift."""
    src = inspect.getsource(plot_import._inspection_password)
    assert "validate_plot_access_password" in src
    for restated in ("4,20", "{4,", "len(pin)", "isdigit"):
        assert restated not in src


# --- preview payload: safe metadata only ----------------------------------

async def test_preview_reports_set_for_a_plot_with_no_credential() -> None:
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)], plot=plot, active=_cycle(), status={},
    )
    row = preview.rows[0]
    assert row.inspection_password_change == "set"
    assert row.inspection_password_configured is False


async def test_preview_reports_replace_for_a_plot_that_already_has_one() -> None:
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)],
        plot=plot, active=_cycle(), status={plot.id: (True, 3)},
    )
    row = preview.rows[0]
    assert row.inspection_password_change == "replace"
    assert row.inspection_password_configured is True


async def test_preview_reports_keep_for_a_blank_cell() -> None:
    plot = _plot()
    preview = await _preview([_update_row()], plot=plot, active=_cycle())
    assert preview.rows[0].inspection_password_change is None


async def test_create_row_with_a_password_is_always_a_set() -> None:
    preview = await _preview([_create_row(newInspectionPassword=PIN)], status={})
    assert preview.rows[0].inspection_password_change == "set"


async def test_preview_payload_carries_no_plaintext_hash_or_digest() -> None:
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)],
        plot=plot, active=_cycle(), status={plot.id: (True, 2)},
    )
    dumped = preview.model_dump_json(by_alias=True)
    assert PIN not in dumped
    assert "$2b$" not in dumped
    for banned in ("password_hash", "passwordHash", "lookupDigest", "newInspectionPassword"):
        assert banned not in dumped
    # the row payload has no password field at all
    assert "newInspectionPassword" not in preview.rows[0].payload.model_dump(by_alias=True)


async def test_preview_never_hashes_and_works_without_a_pepper() -> None:
    """A read-only preview must not need the pepper — nothing is written."""
    plot = _plot()
    with patch(f"{_M}.hash_plot_access_password") as mk_hash, \
         patch(f"{_M}.build_plot_access_password_lookup_digest") as mk_digest:
        preview = await _preview(
            [_update_row(newInspectionPassword=PIN)],
            plot=plot, active=_cycle(), status={},
        )
    assert preview.rows[0].status == "valid"
    mk_hash.assert_not_called()
    mk_digest.assert_not_called()


async def test_preview_issues_no_credential_query_when_no_row_has_a_password() -> None:
    """No N+1 — and no query at all for an ordinary import."""
    p_sup, p_plot, p_active = _patch_lookups(plot=_plot(), active=_cycle())
    with p_sup, p_plot, p_active, _patch_credential_status() as mk:
        await build_preview(object(), _xlsx([_update_row()]), ctx=_ctx())
    mk.assert_not_awaited()


async def test_preview_issues_exactly_one_credential_query_for_many_rows() -> None:
    """Ten password rows → ONE bulk query, never ten."""
    rows = [_update_row(plotCode=f"P{i:03d}", newInspectionPassword=PIN) for i in range(10)]
    p_sup, p_plot, p_active = _patch_lookups(plot=_plot(), active=_cycle())
    with p_sup, p_plot, p_active, _patch_credential_status() as mk:
        await build_preview(object(), _xlsx(rows), ctx=_ctx())
    assert mk.await_count == 1


# --- preview_state binding ------------------------------------------------

async def test_preview_state_binds_every_password_row_without_secrets() -> None:
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)],
        plot=plot, active=_cycle(), status={plot.id: (True, 4)},
    )
    state = preview.preview_state
    assert state is not None
    assert len(state.credential_rows) == 1
    bound = state.credential_rows[0]
    assert bound.row_number == 2
    assert bound.plot_id == plot.id
    assert bound.expected_configured is True
    assert bound.expected_credential_version == 4
    assert bound.intended_change == "replace"
    dumped = state.model_dump_json(by_alias=True)
    assert PIN not in dumped
    assert "$2b$" not in dumped
    assert "digest" not in dumped.lower()


async def test_preview_state_has_no_credential_rows_for_a_blank_file() -> None:
    preview = await _preview([_update_row()], plot=_plot(), active=_cycle())
    assert preview.preview_state.credential_rows == []


# --- commit ---------------------------------------------------------------

def _commit_patches(*, plot, created_plot=None, status=None):
    """Everything commit_import touches for an update_current_cycle row."""
    return (
        patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
              AsyncMock(return_value=_cycle())),
        patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock(return_value=_cycle())),
        patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()),
        _patch_credential_status(status),
        patch(f"{_M}.ActivityLogger", MagicMock(return_value=AsyncMock())),
    )


async def _commit(rows, *, plot, status=None, preview_state=None, ctx=None):
    from contextlib import ExitStack

    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    ps = preview_state
    if ps is None:
        preview = await _preview(rows, plot=plot, active=_cycle(), status=status)
        ps = preview.preview_state
    with ExitStack() as stack:
        for cm in (p_sup, p_plot, p_active, *_commit_patches(plot=plot, status=status)):
            stack.enter_context(cm)
        return await commit_import(
            object(), _xlsx(rows), ctx=ctx or _ctx(), preview_state=ps,
        )


async def test_blank_cell_never_hashes_digests_or_touches_the_repository() -> None:
    plot = _plot()
    with patch(f"{_M}.hash_plot_access_password") as mk_hash, \
         patch(f"{_M}.build_plot_access_password_lookup_digest") as mk_digest, \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        await _commit([_update_row()], plot=plot)
    mk_hash.assert_not_called()
    mk_digest.assert_not_called()
    mk_set.assert_not_awaited()


async def test_set_writes_through_the_shared_repository_with_hash_and_digest() -> None:
    plot = _plot()
    with patch(f"{_M}.build_plot_access_password_lookup_digest", return_value="a" * 64), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=SimpleNamespace(credential_version=1))) as mk_set:
        await _commit([_update_row(newInspectionPassword=PIN)], plot=plot, status={})
    mk_set.assert_awaited_once()
    kwargs = mk_set.await_args.kwargs
    assert kwargs["password_hash"].startswith("$2b$")
    assert PIN not in kwargs["password_hash"]
    assert kwargs["password_lookup_digest"] == "a" * 64
    assert "password" not in {k for k in kwargs if k == "password"}


async def test_replace_uses_the_same_repository_call() -> None:
    plot = _plot()
    with patch(f"{_M}.build_plot_access_password_lookup_digest", return_value="b" * 64), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=SimpleNamespace(credential_version=5))) as mk_set:
        await _commit(
            [_update_row(newInspectionPassword=PIN)], plot=plot, status={plot.id: (True, 4)},
        )
    mk_set.assert_awaited_once()


async def test_hashing_runs_off_the_event_loop_and_before_any_lock() -> None:
    """bcrypt is ~250ms of blocking CPU: it must go through asyncio.to_thread,
    and it must finish BEFORE the plot lock is taken (round 8-9A.1's rule,
    applied to the import's up-front locking)."""
    import asyncio

    plot = _plot()
    order: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy_thread(fn, *args, **kwargs):
        result = await real_to_thread(fn, *args, **kwargs)
        order.append(f"hash:{fn.__name__}")
        return result

    async def spy_lock(*_a, **_kw):
        order.append("lock")
        return plot

    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)], plot=plot, active=_cycle(), status={},
    )
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.asyncio.to_thread", spy_thread), \
         patch(f"{_M}.plot_repo.get_plot_for_update", spy_lock), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.build_plot_access_password_lookup_digest", return_value="c" * 64), \
         _patch_credential_status({}), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=SimpleNamespace(credential_version=1))), \
         patch(f"{_M}.ActivityLogger", MagicMock(return_value=AsyncMock())):
        await commit_import(
            object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
            ctx=_ctx(), preview_state=preview.preview_state,
        )
    assert order == ["hash:hash_plot_access_password", "lock"]


def test_hashing_is_sequential_never_an_unbounded_gather() -> None:
    """A 1000-row file must not launch 1000 concurrent bcrypt rounds."""
    src = inspect.getsource(plot_import._hash_credential_rows)
    # Strip the docstring/comments — "gather" legitimately appears there,
    # explaining why it is NOT used.
    body = src[src.index('"""', src.index('"""') + 3) + 3:]
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )
    assert "gather" not in code
    assert "asyncio.to_thread" in code
    assert "for state in states" in code


async def test_the_same_password_on_two_plots_shares_a_digest_but_not_a_hash() -> None:
    """Deliberate: one entered password must find BOTH plots in round 8-9C
    (same digest), while the stored hashes stay independent (own salt each)."""
    plot_a, plot_b = _plot(plot_code="P001"), _plot(plot_code="P002")
    rows = [
        _update_row(plotCode="P001", newInspectionPassword=PIN),
        _update_row(plotCode="P002", newInspectionPassword=PIN),
    ]
    states = [
        SimpleNamespace(parsed=SimpleNamespace(new_inspection_password=PIN),
                        credential_hash=None, credential_digest=None),
        SimpleNamespace(parsed=SimpleNamespace(new_inspection_password=PIN),
                        credential_hash=None, credential_digest=None),
    ]
    with patch("app.auth.plot_access_password.get_settings", return_value=SimpleNamespace(
        PLOT_ACCESS_PASSWORD_PEPPER=PEPPER,
    )):
        await plot_import._hash_credential_rows(states)
    assert states[0].credential_digest == states[1].credential_digest   # same digest
    assert states[0].credential_hash != states[1].credential_hash       # different salt
    assert all(s.credential_hash.startswith("$2b$") for s in states)
    assert all(PIN not in s.credential_hash for s in states)
    assert plot_a.plot_code != plot_b.plot_code and len(rows) == 2


# --- missing pepper -------------------------------------------------------

async def test_missing_pepper_blocks_a_password_commit_before_any_mutation() -> None:
    from app.auth.plot_access_password import PlotAccessPepperMissingError

    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)], plot=plot, active=_cycle(), status={},
    )
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.build_plot_access_password_lookup_digest",
               side_effect=PlotAccessPepperMissingError("no pepper")), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock()) as mk_lock, \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         _patch_credential_status({}), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(ImportFileError) as exc:
            await commit_import(
                object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
                ctx=_ctx(), preview_state=preview.preview_state,
            )
    # Nothing locked, nothing written — the abort is before every mutation.
    mk_lock.assert_not_awaited()
    mk_update.assert_not_awaited()
    mk_set.assert_not_awaited()
    message = str(exc.value)
    assert "รหัสยืนยันแปลง" in message
    assert "PEPPER" not in message and "pepper" not in message
    assert PIN not in message
    assert "Traceback" not in message


async def test_missing_pepper_does_not_block_an_import_without_passwords() -> None:
    from app.auth.plot_access_password import PlotAccessPepperMissingError

    plot = _plot()
    with patch(f"{_M}.build_plot_access_password_lookup_digest",
               side_effect=PlotAccessPepperMissingError("no pepper")), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        result = await _commit([_update_row()], plot=plot)
    assert result.updated_cycles == 1
    mk_set.assert_not_awaited()


# --- preview-state conflict ----------------------------------------------

async def test_stale_credential_version_rejects_the_whole_file() -> None:
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)],
        plot=plot, active=_cycle(), status={plot.id: (True, 4)},
    )
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.build_plot_access_password_lookup_digest", return_value="d" * 64), \
         patch(f"{_M}.credential_repo.get_credential_status_for_plots",
               AsyncMock(return_value={plot.id: (True, 9)})), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(
                object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
                ctx=_ctx(), preview_state=preview.preview_state,
            )
    mk_update.assert_not_awaited()
    mk_set.assert_not_awaited()
    assert PIN not in str(exc.value)


async def test_a_credential_appearing_after_preview_rejects_the_file() -> None:
    """Preview saw 'set'; someone else set a password in between → the user
    approved a state that no longer exists."""
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)], plot=plot, active=_cycle(), status={},
    )
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.build_plot_access_password_lookup_digest", return_value="e" * 64), \
         patch(f"{_M}.credential_repo.get_credential_status_for_plots",
               AsyncMock(return_value={plot.id: (True, 1)})), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(ImportPreviewStateConflict):
            await commit_import(
                object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
                ctx=_ctx(), preview_state=preview.preview_state,
            )
    mk_set.assert_not_awaited()


async def test_missing_preview_state_rejects_a_password_file() -> None:
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, _patch_credential_status({}), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(ImportPreviewStateConflict):
            await commit_import(
                object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
                ctx=_ctx(), preview_state=None,
            )
    mk_set.assert_not_awaited()


async def test_file_digest_mismatch_rejects_a_password_file() -> None:
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)], plot=plot, active=_cycle(), status={},
    )
    stale = preview.preview_state.model_copy(update={"file_sha256": "0" * 64})
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, _patch_credential_status({}), \
         patch(f"{_M}.hash_plot_access_password") as mk_hash, \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(ImportPreviewStateConflict):
            await commit_import(
                object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
                ctx=_ctx(), preview_state=stale,
            )
    mk_hash.assert_not_called()   # rejected before any bcrypt cost
    mk_set.assert_not_awaited()


# --- rollback -------------------------------------------------------------

async def test_a_failure_after_the_credential_write_aborts_the_whole_file() -> None:
    """All-or-nothing: the credential write shares the import's transaction, so
    a later failure rolls it back with everything else (the caller's get_db
    owns the rollback — here we assert the exception propagates, which is what
    triggers it)."""
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)], plot=plot, active=_cycle(), status={},
    )
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.update_cycle",
               AsyncMock(side_effect=RuntimeError("boom"))), \
         patch(f"{_M}.build_plot_access_password_lookup_digest", return_value="f" * 64), \
         _patch_credential_status({}), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=SimpleNamespace(credential_version=1))), \
         patch(f"{_M}.ActivityLogger", MagicMock(return_value=AsyncMock())):
        with pytest.raises(RuntimeError):
            await commit_import(
                object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
                ctx=_ctx(), preview_state=preview.preview_state,
            )


async def test_credential_repository_failure_aborts_the_import_action() -> None:
    plot = _plot()
    preview = await _preview(
        [_update_row(newInspectionPassword=PIN)], plot=plot, active=_cycle(), status={},
    )
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.build_plot_access_password_lookup_digest", return_value="0" * 64), \
         _patch_credential_status({}), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(side_effect=RuntimeError("db down"))), \
         patch(f"{_M}.ActivityLogger", MagicMock(return_value=AsyncMock())):
        with pytest.raises(RuntimeError):
            await commit_import(
                object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
                ctx=_ctx(), preview_state=preview.preview_state,
            )


# --- activity log ---------------------------------------------------------

async def test_credential_change_writes_a_high_risk_security_event_without_secrets() -> None:
    plot = _plot()
    logger = AsyncMock()
    with patch(f"{_M}.build_plot_access_password_lookup_digest", return_value="1" * 64), \
         patch(f"{_M}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=SimpleNamespace(credential_version=2))), \
         patch(f"{_M}.ActivityLogger", MagicMock(return_value=logger)):
        p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
        preview = await _preview(
            [_update_row(newInspectionPassword=PIN)], plot=plot, active=_cycle(), status={},
        )
        with p_sup, p_plot, p_active, \
             patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
             patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
                   AsyncMock(return_value=_cycle())), \
             patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
             _patch_credential_status({}):
            await commit_import(
                object(), _xlsx([_update_row(newInspectionPassword=PIN)]),
                ctx=_ctx(), preview_state=preview.preview_state,
            )
    kwargs = logger.log.await_args.kwargs
    assert kwargs["action"] == "plot.inspection_access_credential_set"
    assert kwargs["is_security_event"] is True
    assert kwargs["risk_level"] == "high"
    assert kwargs["resource_id"] == str(plot.id)
    logged = str(kwargs)
    for leaked in (PIN, "$2b$", "1" * 64, "phone"):
        assert leaked not in logged


async def test_a_blank_row_writes_no_credential_activity_event() -> None:
    plot = _plot()
    logger = AsyncMock()
    with patch(f"{_M}.ActivityLogger", MagicMock(return_value=logger)):
        await _commit([_update_row()], plot=plot)
    logger.log.assert_not_awaited()


# --- result workbook ------------------------------------------------------

def _view(**over) -> dict:
    base = {
        "row_number": 2, "action": "update_current_cycle", "status": "valid",
        "message": "", "error_code": None, "result_cycle_no": 1,
        "resolved_action": None, "lot_mode": None, "proposed_lot_no": None,
        "result_lot_no": None, "result_lot_no_source": None,
        "result_lot_running_no": None, "warning": None,
        "credential_change": None, "credential_configured": False,
        "raw": {"action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P001"},
    }
    base.update(over)
    return base


def _workbook_text(workbook: bytes) -> str:
    """Every worksheet's XML concatenated — what a scan of the whole file
    would see."""
    with ZipFile(BytesIO(workbook)) as zf:
        return "".join(
            zf.read(name).decode("utf-8") for name in zf.namelist()
            if name.endswith(".xml")
        )


def test_report_row_view_strips_the_password_at_the_source() -> None:
    state = SimpleNamespace(
        row_number=2, parsed=SimpleNamespace(action="update_current_cycle"),
        errors=["boom"], error_code=None, result_cycle_no=None, resolved_action=None,
        lot_mode=None, proposed_lot_no=None, result_lot_no=None,
        result_lot_no_source=None, result_lot_running_no=None, final_warning=None,
        # round 8-10B — informational final_plot note; None for other actions
        final_record_note=None,
        credential_change="set", credential_configured=False,
        raw={"plotCode": "P001", "newInspectionPassword": PIN},
    )
    view = plot_import.report_row_view(state)
    assert "newInspectionPassword" not in view["raw"]
    assert PIN not in str(view)


@pytest.mark.parametrize("completed", [False, True])
def test_no_worksheet_in_the_result_workbook_contains_the_password(completed: bool) -> None:
    """Automated scan: open every sheet, read every cell, assert the test PIN
    is nowhere in the file."""
    views = [
        _view(raw={"plotCode": "P001", "newInspectionPassword": PIN}, credential_change="set"),
        _view(row_number=3, status="error", message="บางอย่างผิดพลาด",
              raw={"plotCode": "P002", "newInspectionPassword": PIN_LONG},
              credential_change="replace"),
    ]
    workbook = plot_import_report.build_plot_import_result_workbook(
        views,
        phase=plot_import_report.PHASE_COMMIT if completed else plot_import_report.PHASE_PREVIEW,
        completed=completed,
        original_filename="upload.xlsx",
        processed_at=datetime.datetime(2026, 8, 3, tzinfo=datetime.timezone.utc),
    )
    text = _workbook_text(workbook)
    assert PIN not in text
    assert PIN_LONG not in text
    assert "$2b$" not in text
    assert "digest" not in text.lower()


def test_result_workbook_reports_safe_password_statuses() -> None:
    views = [
        _view(credential_change=None),
        _view(row_number=3, credential_change="set"),
        _view(row_number=4, credential_change="replace"),
        _view(row_number=5, status="error", message="err", credential_change="set"),
    ]
    workbook = plot_import_report.build_plot_import_result_workbook(
        views, phase=plot_import_report.PHASE_COMMIT, completed=True,
        original_filename="upload.xlsx",
        processed_at=datetime.datetime(2026, 8, 3, tzinfo=datetime.timezone.utc),
    )
    text = _workbook_text(workbook)
    assert plot_import_report.CREDENTIAL_RESULT_KEEP in text
    assert plot_import_report.CREDENTIAL_RESULT_SET in text
    assert plot_import_report.CREDENTIAL_RESULT_REPLACED in text
    assert plot_import_report.CREDENTIAL_RESULT_FAILED in text


def test_result_workbook_has_a_password_result_column_and_no_input_echo() -> None:
    assert "resultInspectionPassword" in plot_import_report.RESULT_COLUMNS
    assert "newInspectionPassword" in plot_import_report.ALL_COLUMNS   # header only
    src = inspect.getsource(plot_import_report._result_sheet_xml)
    assert "COLUMN_NEW_INSPECTION_PASSWORD" in src   # explicitly blanked


# --- template export security (Part C) ------------------------------------

def _template_sheets(plots, excluded=None, credential_status=None) -> dict[str, str]:
    from app.api.v1.plots import _contextual_plot_template_workbook

    content = _contextual_plot_template_workbook(
        plots, excluded or [], credential_status=credential_status or {},
    )
    with ZipFile(BytesIO(content)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".xml")}


def _template_plot(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), plot_code="P001", name="แปลงหนึ่ง", is_active=True,
        village=None, district=None, province="เชียงใหม่",
        latitude=None, longitude=None, rai=None,
        supplier=SimpleNamespace(id=uuid4(), code="SUP001", name="ซัพ", is_active=True),
        active_cycle=None, access_phones=[], cycles=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_template_exports_only_the_configured_status_never_a_secret() -> None:
    plot = _template_plot()
    sheets = _template_sheets([plot], credential_status={plot.id: (True, 7)})
    text = "".join(sheets.values())
    assert "configured" in text
    # the version is bookkeeping and is never exported
    assert ">7<" not in text
    for banned in ("$2b$", "passwordHash", "lookupDigest", "credentialVersion"):
        assert banned not in text


def test_template_new_password_cells_are_blank_for_current_data_rows() -> None:
    """A downloaded template must never carry an existing password (or an
    example one) on a REAL plot row — only the highlighted example sheet."""
    from app.api.v1.plots import _new_cycle_row_values, _reactivate_row_values

    plot = _template_plot()
    for values in (
        _new_cycle_row_values(plot, password_configured=True),
        _reactivate_row_values(plot, None, password_configured=True),
    ):
        assert values["newInspectionPassword"] is None
        assert values["inspectionPasswordStatus"] == "configured"


def test_not_configured_plots_report_that_status() -> None:
    from app.api.v1.plots import _new_cycle_row_values

    values = _new_cycle_row_values(_template_plot(), password_configured=False)
    assert values["inspectionPasswordStatus"] == "not_configured"


def test_example_passwords_live_only_in_the_example_rows() -> None:
    from app.api.v1.plots import _template_example_rows

    examples = _template_example_rows("SUP001")
    given = [e.get("newInspectionPassword") for e in examples if e.get("newInspectionPassword")]
    assert given, "the examples should demonstrate the column"
    for pin in given:
        # a legal, obviously-fake example value
        assert pin.isdigit() and 4 <= len(pin) <= 20


def test_current_snapshot_and_excluded_sheets_carry_status_only() -> None:
    from app.api.v1.plots import _current_snapshot_row_values, _excluded_row_values

    plot = _template_plot()
    snap = _current_snapshot_row_values(plot, password_configured=True)
    excl = _excluded_row_values(plot, "all", password_configured=False)
    assert snap["inspectionPasswordStatus"] == "configured"
    assert excl["inspectionPasswordStatus"] == "not_configured"
    for values in (snap, excl):
        assert "newInspectionPassword" not in values
        assert not any("hash" in k.lower() or "digest" in k.lower() for k in values)
