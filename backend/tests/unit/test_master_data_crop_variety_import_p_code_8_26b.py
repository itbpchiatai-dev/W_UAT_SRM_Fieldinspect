"""Master Data crop/variety Excel import — the pCode column (round 8-26B).

Same DB-free style as test_master_data_crop_variety_import_service.py: both
batch lookups (list_by_type_values, list_by_type_parents) and create/update
are AsyncMocks; the workbook bytes are built and parsed by the real
hand-rolled writer/reader.

The rules this locks in (all confirmed with the user before the round):
  - a P.Code belongs to the row's VARIETY, so pCode with a blank variety is
    an error
  - blank pCode PRESERVES whatever the variety has — it never removes one,
    because a P.Code is embedded verbatim in every Lot No generated from it
  - a variety owns at most ONE ACTIVE P.Code
  - a P.Code is never re-parented to a different variety
  - re-typing a DEACTIVATED P.Code into a now-free slot ACTIVATES it, rather
    than silently doing nothing
  - the P.Code plan is a SEPARATE per-row action from the crop/variety one,
    so one row can create a crop, a variety and a P.Code at once
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import master_data_crop_variety_import as cv_import
from app.services.excel_reader import read_first_sheet
from app.services.excel_workbook import build_xlsx

_REPO = "app.services.master_data_crop_variety_import.repo"
_PC = cv_import.P_CODE_TYPE


def _md(type_, value, parent=None, active=True):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active)


def _xlsx(rows: list[dict]) -> bytes:
    cols = cv_import.IMPORT_COLUMNS
    data = [cols, [cv_import.TEMPLATE_DESCRIPTION_MARKER, *(["คำอธิบาย"] * (len(cols) - 1))]]
    for r in rows:
        data.append([r.get(c, "") for c in cols])
    return build_xlsx([(cv_import.SHEET_NAME, data)])


def _row(crop=None, variety=None, p_code=None, status=None) -> dict:
    d: dict = {}
    if crop is not None:
        d["crop"] = crop
    if variety is not None:
        d["variety"] = variety
    if p_code is not None:
        d["pCode"] = p_code
    if status is not None:
        d["varietyStatus"] = status
    return d


class _both:
    def __init__(self, *managers):
        self._managers = managers

    def __enter__(self):
        return [m.__enter__() for m in self._managers]

    def __exit__(self, *exc):
        for m in reversed(self._managers):
            m.__exit__(*exc)
        return False


def _patch_lookup(crops=None, varieties=None, p_codes=None):
    pools = {"crop": crops or [], "variety": varieties or [], _PC: p_codes or []}

    async def fake_values(db, type, values):
        return [m for m in pools.get(type, []) if m.value in values]

    async def fake_parents(db, type, parents):
        return [m for m in pools.get(type, []) if m.parent in parents]

    return _both(
        patch(f"{_REPO}.list_by_type_values", AsyncMock(side_effect=fake_values)),
        patch(f"{_REPO}.list_by_type_parents", AsyncMock(side_effect=fake_parents)),
    )


def _patch_writes():
    async def fake_create(db, payload):
        return SimpleNamespace(
            type=payload.type, value=payload.value, parent=payload.parent, active=True,
        )

    async def fake_update(db, item, payload):
        if payload.active is not None:
            item.active = payload.active
        return item

    return AsyncMock(side_effect=fake_create), AsyncMock(side_effect=fake_update)


def _row_of(preview, row_number: int):
    return next(r for r in preview.rows if r.row_number == row_number)


async def _preview(rows: list[dict], **pools):
    with _patch_lookup(**pools):
        return await cv_import.build_preview(AsyncMock(), _xlsx(rows))


_CROP = _md("crop", "พริก")
_VARIETY = _md("variety", "พริกขี้หนู", parent="พริก")
_VARIETY2 = _md("variety", "พริกจินดา", parent="พริก")


# --- the column exists at all ------------------------------------------


async def test_pcode_sits_between_variety_and_variety_status() -> None:
    assert cv_import.IMPORT_COLUMNS == ["crop", "variety", "pCode", "varietyStatus"]


async def test_an_old_three_column_file_is_rejected_whole_not_half_imported() -> None:
    """The header SET is compared exactly, so a pre-8-26B file fails with one
    clear "use the new template" message rather than importing with pCode
    silently missing from every row."""
    old_cols = ["crop", "variety", "varietyStatus"]
    data = [old_cols, [cv_import.TEMPLATE_DESCRIPTION_MARKER, "ค", "ค"], ["พริก", "พริกขี้หนู", ""]]
    content = build_xlsx([(cv_import.SHEET_NAME, data)])
    with _patch_lookup(crops=[_CROP], varieties=[_VARIETY]):
        try:
            await cv_import.build_preview(AsyncMock(), content)
        except cv_import.CropVarietyImportFileError as exc:
            assert "Template" in str(exc)
        else:
            raise AssertionError("an old 3-column file must be rejected")


# --- validation ---------------------------------------------------------


async def test_pcode_without_a_variety_is_an_error() -> None:
    preview = await _preview([_row(crop="พริก", p_code="WM-111")], crops=[_CROP])
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "pCode มีค่าแต่ variety ว่าง" in row.error_message


async def test_blank_pcode_leaves_an_existing_pcode_alone() -> None:
    """The whole point of blank-preserves: a template round-trip that edits
    only varietyStatus must not disturb the variety's P.Code."""
    existing = _md(_PC, "WM-111", parent="พริกขี้หนู")
    preview = await _preview(
        [_row(crop="พริก", variety="พริกขี้หนู")],
        crops=[_CROP], varieties=[_VARIETY], p_codes=[existing],
    )
    row = _row_of(preview, 3)
    assert row.row_status == "SKIPPED"
    assert row.p_code_action == "none"
    assert preview.summary.p_codes_to_create == 0


async def test_same_pcode_on_two_rows_is_a_duplicate_error_on_both() -> None:
    preview = await _preview(
        [
            _row(crop="พริก", variety="พริกขี้หนู", p_code="WM-111"),
            _row(crop="พริก", variety="พริกจินดา", p_code="WM-111"),
        ],
        crops=[_CROP], varieties=[_VARIETY, _VARIETY2],
    )
    for row_number in (3, 4):
        row = _row_of(preview, row_number)
        assert row.row_status == "ERROR"
        assert "pCode นี้ซ้ำกันในไฟล์" in row.error_message


async def test_pcode_already_bound_to_another_variety_is_never_reparented() -> None:
    taken = _md(_PC, "WM-111", parent="พริกขี้หนู")
    preview = await _preview(
        [_row(crop="พริก", variety="พริกจินดา", p_code="WM-111")],
        crops=[_CROP], varieties=[_VARIETY, _VARIETY2], p_codes=[taken],
    )
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "ผูกกับพันธุ์อื่นอยู่แล้ว" in row.error_message
    assert "พริกขี้หนู" in row.error_message


async def test_a_second_pcode_for_a_variety_that_already_has_an_active_one_is_an_error() -> None:
    holder = _md(_PC, "WM-111", parent="พริกขี้หนู", active=True)
    preview = await _preview(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-999")],
        crops=[_CROP], varieties=[_VARIETY], p_codes=[holder],
    )
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "WM-111" in row.error_message
    assert "1 พันธุ์มีได้เพียง 1 P.Code" in row.error_message


async def test_the_occupying_pcode_is_found_even_when_the_file_never_mentions_it() -> None:
    """The slot check is keyed by PARENT, so it catches a P.Code the uploaded
    file has no row for — which a value-keyed lookup alone would miss."""
    holder = _md(_PC, "SOMETHING-ELSE", parent="พริกขี้หนู", active=True)
    preview = await _preview(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-999")],
        crops=[_CROP], varieties=[_VARIETY], p_codes=[holder],
    )
    assert _row_of(preview, 3).row_status == "ERROR"
    assert "SOMETHING-ELSE" in _row_of(preview, 3).error_message


async def test_an_inactive_pcode_does_not_occupy_the_slot() -> None:
    retired = _md(_PC, "WM-111", parent="พริกขี้หนู", active=False)
    preview = await _preview(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-999")],
        crops=[_CROP], varieties=[_VARIETY], p_codes=[retired],
    )
    row = _row_of(preview, 3)
    assert row.row_status == "READY"
    assert row.p_code_action == "create_p_code"


async def test_retyping_a_deactivated_pcode_into_a_free_slot_activates_it() -> None:
    """Not a silent no-op: the user typed a value, so something must happen."""
    retired = _md(_PC, "WM-111", parent="พริกขี้หนู", active=False)
    preview = await _preview(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-111")],
        crops=[_CROP], varieties=[_VARIETY], p_codes=[retired],
    )
    row = _row_of(preview, 3)
    assert row.row_status == "READY"
    assert row.p_code_action == "activate_p_code"
    assert preview.summary.p_codes_to_activate == 1


async def test_a_pcode_too_long_is_an_error() -> None:
    preview = await _preview(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="x" * 256)],
        crops=[_CROP], varieties=[_VARIETY],
    )
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "pCode ต้องไม่เกิน 255" in row.error_message


async def test_an_error_row_carries_no_pcode_plan() -> None:
    """An inactive crop fails the row; the P.Code half must not survive as a
    READY-looking plan that the commit could execute."""
    inactive_crop = _md("crop", "พริก", active=False)
    preview = await _preview(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-999")],
        crops=[inactive_crop], varieties=[_VARIETY],
    )
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert row.p_code_action == "none"
    assert preview.summary.p_codes_to_create == 0


# --- row status / action independence ----------------------------------


async def test_a_row_is_ready_for_its_pcode_alone_even_when_the_variety_is_unchanged() -> None:
    preview = await _preview(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-111")],
        crops=[_CROP], varieties=[_VARIETY],
    )
    row = _row_of(preview, 3)
    assert row.row_status == "READY"
    assert row.action == "none"           # crop + variety both already exist
    assert row.p_code_action == "create_p_code"


async def test_one_row_can_plan_a_crop_a_variety_and_a_pcode_at_once() -> None:
    preview = await _preview([_row(crop="แตงโม", variety="แตงโมกินรี", p_code="WM-777")])
    row = _row_of(preview, 3)
    assert row.row_status == "READY"
    assert row.action == "create_crop_and_variety"
    assert row.p_code_action == "create_p_code"
    assert preview.summary.crops_to_create == 1
    assert preview.summary.varieties_to_create == 1
    assert preview.summary.p_codes_to_create == 1


# --- commit -------------------------------------------------------------


async def _commit(rows: list[dict], **pools):
    content = _xlsx(rows)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(**pools):
        preview = await cv_import.build_preview(AsyncMock(), content)
    with _patch_lookup(**pools), \
         patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(
            AsyncMock(), content, preview_state=preview.preview_state,
        )
    return result, create_mock, update_mock


async def test_commit_creates_the_pcode_under_its_variety() -> None:
    result, create_mock, _ = await _commit(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-999")],
        crops=[_CROP], varieties=[_VARIETY],
    )
    assert result.created_p_codes == 1
    payloads = [c.args[1] for c in create_mock.await_args_list]
    p_code_payloads = [p for p in payloads if p.type == _PC]
    assert len(p_code_payloads) == 1
    assert p_code_payloads[0].value == "WM-999"
    assert p_code_payloads[0].parent == "พริกขี้หนู"


async def test_commit_creates_crop_then_variety_then_pcode() -> None:
    """Execution order is a business requirement — `parent` carries no FK, so
    nothing but this ordering makes a single all-new row resolve its own
    parents in a way a human reading master_data would call correct."""
    _result, create_mock, _ = await _commit([_row(crop="แตงโม", variety="แตงโมกินรี", p_code="WM-777")])
    types = [c.args[1].type for c in create_mock.await_args_list]
    assert types == ["crop", "variety", _PC]


async def test_commit_activates_a_deactivated_pcode() -> None:
    retired = _md(_PC, "WM-111", parent="พริกขี้หนู", active=False)
    result, create_mock, update_mock = await _commit(
        [_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-111")],
        crops=[_CROP], varieties=[_VARIETY], p_codes=[retired],
    )
    assert result.activated_p_codes == 1
    assert result.created_p_codes == 0
    assert not [c for c in create_mock.await_args_list if c.args[1].type == _PC]
    assert update_mock.await_args_list[0].args[2].active is True


async def test_commit_never_writes_a_pcode_for_a_blank_cell() -> None:
    result, create_mock, update_mock = await _commit(
        [_row(crop="พริก", variety="พริกขี้หนู")],
        crops=[_CROP], varieties=[_VARIETY],
    )
    assert result.created_p_codes == 0
    assert result.activated_p_codes == 0
    create_mock.assert_not_awaited()
    update_mock.assert_not_awaited()


# --- template pre-fill --------------------------------------------------


def _patch_list_items(crops, varieties, p_codes=None):
    async def fake(db, type=None, parent=None, active_only=False):
        if type == "crop":
            return crops
        if type == "variety":
            return varieties
        if type == _PC:
            return [m for m in (p_codes or []) if m.active] if active_only else (p_codes or [])
        return []
    return patch(f"{_REPO}.list_items", AsyncMock(side_effect=fake))


async def test_template_prefills_the_active_pcode_of_each_variety() -> None:
    active = _md(_PC, "WM-111", parent="พริกขี้หนู", active=True)
    with _patch_list_items([_CROP], [_VARIETY, _VARIETY2], [active]):
        content = await cv_import.build_template(db=AsyncMock())
    _headers, rows = read_first_sheet(content)
    data = {r["variety"]: r.get("pCode") for _n, r in rows[1:]}
    assert data["พริกขี้หนู"] == "WM-111"
    # A variety with none gets a blank cell, which on the way back in means
    # "leave it alone" — so an untouched round-trip is a no-op.
    assert data["พริกจินดา"] in (None, "")


async def test_template_never_prefills_a_deactivated_pcode() -> None:
    """Otherwise re-uploading the template untouched would silently
    resurrect a P.Code an admin had deliberately retired."""
    retired = _md(_PC, "WM-111", parent="พริกขี้หนู", active=False)
    with _patch_list_items([_CROP], [_VARIETY], [retired]):
        content = await cv_import.build_template(db=AsyncMock())
    _headers, rows = read_first_sheet(content)
    data = {r["variety"]: r.get("pCode") for _n, r in rows[1:]}
    assert data["พริกขี้หนู"] in (None, "")


# --- drift detection ----------------------------------------------------


async def test_another_admin_taking_the_slot_between_preview_and_commit_is_a_409() -> None:
    """Recording the variety's active P.Code in the preview state — even
    though this file never names it — is what makes this a clean state
    conflict instead of a late row error."""
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู", p_code="WM-999")])
    with _patch_lookup(crops=[_CROP], varieties=[_VARIETY]):
        preview = await cv_import.build_preview(AsyncMock(), content)

    sneaked_in = _md(_PC, "WM-111", parent="พริกขี้หนู", active=True)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(crops=[_CROP], varieties=[_VARIETY], p_codes=[sneaked_in]), \
         patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        try:
            await cv_import.commit(AsyncMock(), content, preview_state=preview.preview_state)
        except cv_import.CropVarietyImportStateConflict:
            pass
        else:
            raise AssertionError("expected a state conflict")
    create_mock.assert_not_awaited()
    update_mock.assert_not_awaited()
