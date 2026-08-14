"""Master Data crop/variety Excel import — validation + commit (round
8-15A, brief items 9-36). DB-free: master_data_repository.list_by_type_values
/create/update are patched with AsyncMocks — never a real database, and
`commit()` is never exercised against the LIVE dev DB (matches the round's
"ห้าม Live Commit" — these are automated pytest mocks, not a live HTTP call).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import master_data_crop_variety_import as cv_import
from app.services.excel_workbook import build_xlsx

_REPO = "app.services.master_data_crop_variety_import.repo"


def _md(type_, value, parent=None, active=True):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active)


def _xlsx(rows: list[dict], *, headers: list[str] | None = None) -> bytes:
    """Header (row 1) + a real description row (row 2, matching what the
    real template ships and what the importer must skip — see the module's
    own item-2 template test) + one data row per entry starting at row 3, the
    same shape a real downloaded-edited-reuploaded file has."""
    cols = headers or cv_import.IMPORT_COLUMNS
    data = [cols, [cv_import.TEMPLATE_DESCRIPTION_MARKER, "คำอธิบาย", "คำอธิบาย"]]
    for r in rows:
        data.append([r.get(c, "") for c in cols])
    return build_xlsx([(cv_import.SHEET_NAME, data)])


def _row(crop=None, variety=None, status=None) -> dict:
    d: dict = {}
    if crop is not None:
        d["crop"] = crop
    if variety is not None:
        d["variety"] = variety
    if status is not None:
        d["varietyStatus"] = status
    return d


def _patch_lookup(existing_crops=None, existing_varieties=None):
    existing_crops = existing_crops or []
    existing_varieties = existing_varieties or []

    async def fake(db, type, values):
        pool = existing_crops if type == "crop" else existing_varieties
        return [m for m in pool if m.value in values]

    return patch(f"{_REPO}.list_by_type_values", AsyncMock(side_effect=fake))


def _patch_writes():
    """create()/update() as AsyncMocks that behave plausibly without a DB —
    used only by commit tests. Returns (create_mock, update_mock)."""
    created: list[SimpleNamespace] = []

    async def fake_create(db, payload):
        item = SimpleNamespace(type=payload.type, value=payload.value, parent=payload.parent, active=True)
        created.append(item)
        return item

    async def fake_update(db, item, payload):
        if payload.active is not None:
            item.active = payload.active
        return item

    return AsyncMock(side_effect=fake_create), AsyncMock(side_effect=fake_update)


def _row_of(preview, row_number: int):
    return next(r for r in preview.rows if r.row_number == row_number)


# --- Validation (items 9-23) ------------------------------------------------

async def test_item9_crop_required():
    content = _xlsx([_row(variety="พริกขี้หนู")])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "ต้องระบุ crop" in row.error_message


async def test_item10_variety_optional_crop_only_row_is_valid():
    crop = _md("crop", "ฟักทอง")
    content = _xlsx([_row(crop="ฟักทอง")])
    with _patch_lookup(existing_crops=[crop]):
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "SKIPPED"  # crop already exists active — no-op
    assert row.error_message == ""


async def test_item11_blank_status_defaults_to_active():
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู")])  # no status
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "READY"
    assert preview.preview_state.rows[0].variety_status is True


async def test_item12_invalid_status_is_error():
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู", status="ไม่ทราบ")])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "varietyStatus" in row.error_message


async def test_item13_status_with_blank_variety_is_error():
    content = _xlsx([_row(crop="พริก", status="เปิดใช้งาน")])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "varietyStatus" in row.error_message


async def test_item14_duplicate_crop_variety_pair_errors_every_row():
    content = _xlsx([
        _row(crop="พริก", variety="พริกขี้หนู"),
        _row(crop="พริก", variety="พริกขี้หนู"),
    ])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    assert _row_of(preview, 3).row_status == "ERROR"
    assert _row_of(preview, 4).row_status == "ERROR"


async def test_item15_same_variety_under_different_crops_errors_both():
    content = _xlsx([
        _row(crop="พริก", variety="มะม่วง"),
        _row(crop="เมล่อน", variety="มะม่วง"),
    ])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    assert _row_of(preview, 3).row_status == "ERROR"
    assert _row_of(preview, 4).row_status == "ERROR"


async def test_item14b_duplicate_crop_only_row_errors_every_row():
    content = _xlsx([_row(crop="พริก"), _row(crop="พริก")])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    assert _row_of(preview, 3).row_status == "ERROR"
    assert _row_of(preview, 4).row_status == "ERROR"


async def test_item14c_same_crop_multiple_varieties_is_not_a_duplicate():
    """crop repeated because of several varieties is NORMAL — no error."""
    content = _xlsx([
        _row(crop="พริก", variety="พริกขี้หนู"),
        _row(crop="พริก", variety="พริกจินดา"),
    ])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    assert _row_of(preview, 3).row_status == "READY"
    assert _row_of(preview, 4).row_status == "READY"
    assert preview.summary.crops_to_create == 1  # deduped, not 2


async def test_item16_length_limits():
    long_crop = "ก" * 256
    long_variety = "ข" * 256
    content = _xlsx([_row(crop=long_crop, variety=long_variety)])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "255" in row.error_message


async def test_item17_invalid_header_raises_file_error():
    bad = build_xlsx([(cv_import.SHEET_NAME, [["foo", "bar", "baz"]])])
    with _patch_lookup(), pytest.raises(cv_import.CropVarietyImportFileError):
        await cv_import.build_preview(AsyncMock(), bad)


async def test_item17b_not_an_xlsx_file_raises_file_error():
    with _patch_lookup(), pytest.raises(cv_import.CropVarietyImportFileError):
        await cv_import.build_preview(AsyncMock(), b"not a zip file at all")


async def test_item18_inactive_crop_is_error_with_exact_message():
    crop = _md("crop", "พริก", active=False)
    content = _xlsx([_row(crop="พริก")])
    with _patch_lookup(existing_crops=[crop]):
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert row.error_message == cv_import._MSG_CROP_INACTIVE
    assert row.error_message == "ชนิดพืชนี้ปิดใช้งานอยู่ กรุณาเปิดใช้งานผ่านหน้า Master Data ก่อน"


async def test_item19_existing_identical_row_is_skipped():
    crop = _md("crop", "พริก", active=True)
    variety = _md("variety", "พริกขี้หนู", parent="พริก", active=True)
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู", status="เปิดใช้งาน")])
    with _patch_lookup(existing_crops=[crop], existing_varieties=[variety]):
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "SKIPPED"
    assert row.action == cv_import.ACTION_NONE


async def test_item20_new_crop_and_variety_is_ready():
    content = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง")])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "READY"
    assert row.action == cv_import.ACTION_CREATE_CROP_AND_VARIETY


async def test_item21_status_change_is_ready_to_activate_or_deactivate():
    crop = _md("crop", "พริก", active=True)
    variety = _md("variety", "พริกขี้หนู", parent="พริก", active=True)
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู", status="ปิดใช้งาน")])
    with _patch_lookup(existing_crops=[crop], existing_varieties=[variety]):
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "READY"
    assert row.action == cv_import.ACTION_DEACTIVATE_VARIETY


async def test_item22_existing_variety_under_different_crop_is_error():
    crop = _md("crop", "เมล่อน", active=True)
    variety = _md("variety", "พริกขี้หนู", parent="พริก", active=True)  # bound to พริก already
    content = _xlsx([_row(crop="เมล่อน", variety="พริกขี้หนู")])
    with _patch_lookup(existing_crops=[crop], existing_varieties=[variety]):
        preview = await cv_import.build_preview(AsyncMock(), content)
    row = _row_of(preview, 3)
    assert row.row_status == "ERROR"
    assert "พืชอื่น" in row.error_message


async def test_item23_preview_never_mutates_the_db():
    content = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง")])
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        await cv_import.build_preview(AsyncMock(), content)
    create_mock.assert_not_called()
    update_mock.assert_not_called()


# --- Commit (items 24-36) ---------------------------------------------------

async def _commit_with_matching_state(content: bytes, *, existing_crops=None, existing_varieties=None):
    """Build a real preview (to get a genuinely-matching previewState), then
    commit against the SAME content/state — the shared, DB-free happy path
    every commit test below starts from."""
    with _patch_lookup(existing_crops=existing_crops, existing_varieties=existing_varieties):
        preview = await cv_import.build_preview(AsyncMock(), content)
    return preview.preview_state


async def test_item24_creates_crop_before_variety():
    content = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง")])
    state = await _commit_with_matching_state(content)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        await cv_import.commit(AsyncMock(), content, preview_state=state)
    types_in_order = [call.args[1].type for call in create_mock.call_args_list]
    assert types_in_order == ["crop", "variety"]


async def test_item25_creates_active_variety():
    content = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง", status="เปิดใช้งาน")])
    state = await _commit_with_matching_state(content)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(AsyncMock(), content, preview_state=state)
    assert result.created_varieties == 1
    update_mock.assert_not_called()  # active is the ORM default — no follow-up flush needed


async def test_item26_creates_inactive_variety():
    content = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง", status="ปิดใช้งาน")])
    state = await _commit_with_matching_state(content)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(AsyncMock(), content, preview_state=state)
    assert result.created_varieties == 1
    update_mock.assert_called_once()
    assert update_mock.call_args.args[2].active is False


async def test_item27_activates_existing_variety():
    crop = _md("crop", "พริก", active=True)
    variety = _md("variety", "พริกขี้หนู", parent="พริก", active=False)
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู", status="เปิดใช้งาน")])
    state = await _commit_with_matching_state(content, existing_crops=[crop], existing_varieties=[variety])
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(existing_crops=[crop], existing_varieties=[variety]), \
         patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(AsyncMock(), content, preview_state=state)
    assert result.activated_varieties == 1
    create_mock.assert_not_called()
    assert update_mock.call_args.args[2].active is True


async def test_item28_deactivates_existing_variety():
    crop = _md("crop", "พริก", active=True)
    variety = _md("variety", "พริกขี้หนู", parent="พริก", active=True)
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู", status="ปิดใช้งาน")])
    state = await _commit_with_matching_state(content, existing_crops=[crop], existing_varieties=[variety])
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(existing_crops=[crop], existing_varieties=[variety]), \
         patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(AsyncMock(), content, preview_state=state)
    assert result.deactivated_varieties == 1
    assert update_mock.call_args.args[2].active is False


async def test_item29_unchanged_row_is_skipped_no_write():
    crop = _md("crop", "พริก", active=True)
    variety = _md("variety", "พริกขี้หนู", parent="พริก", active=True)
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู", status="เปิดใช้งาน")])
    state = await _commit_with_matching_state(content, existing_crops=[crop], existing_varieties=[variety])
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(existing_crops=[crop], existing_varieties=[variety]), \
         patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(AsyncMock(), content, preview_state=state)
    assert result.skipped_rows == 1
    create_mock.assert_not_called()
    update_mock.assert_not_called()


async def test_item30_commit_refuses_when_any_row_has_an_error():
    content = _xlsx([
        _row(crop="พริก", variety="พริกขี้หนู"),
        _row(crop="เมล่อน", variety="มะม่วง", status="ไม่ทราบ"),  # invalid status
    ])
    state = await _commit_with_matching_state(content)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        with pytest.raises(cv_import.CropVarietyImportHasErrors):
            await cv_import.commit(AsyncMock(), content, preview_state=state)
    create_mock.assert_not_called()
    update_mock.assert_not_called()


async def test_item31_missing_or_stale_preview_state_is_409_conflict():
    content = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง")])
    with _patch_lookup(), pytest.raises(cv_import.CropVarietyImportStateConflict):
        await cv_import.commit(AsyncMock(), content, preview_state=None)


async def test_item32_changed_file_digest_is_409_conflict():
    content_a = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง")])
    content_b = _xlsx([_row(crop="สับปะรด", variety="สับปะรดภูแล")])
    state_a = await _commit_with_matching_state(content_a)
    with _patch_lookup(), pytest.raises(cv_import.CropVarietyImportStateConflict):
        await cv_import.commit(AsyncMock(), content_b, preview_state=state_a)


async def test_item32b_db_state_drift_since_preview_is_409_conflict():
    """Same file, but master_data changed underneath between Preview and
    Commit (another admin created the crop meanwhile)."""
    content = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง")])
    state = await _commit_with_matching_state(content)  # previewed when crop didn't exist
    now_existing_crop = _md("crop", "มังคุด", active=True)  # ...now it does
    with _patch_lookup(existing_crops=[now_existing_crop]), pytest.raises(cv_import.CropVarietyImportStateConflict):
        await cv_import.commit(AsyncMock(), content, preview_state=state)


async def test_item33_integrity_error_propagates_uncaught_for_caller_to_roll_back():
    content = _xlsx([_row(crop="มังคุด", variety="มังคุดทอง")])
    state = await _commit_with_matching_state(content)
    create_mock = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock):
        with pytest.raises(IntegrityError):
            await cv_import.commit(AsyncMock(), content, preview_state=state)


async def test_item34_all_or_nothing_no_partial_writes_on_error_row():
    """One error row among otherwise-valid rows still blocks EVERY write —
    not just the errored row's own."""
    content = _xlsx([
        _row(crop="มังคุด", variety="มังคุดทอง"),  # would be valid alone
        _row(crop="เมล่อน", variety="มะม่วง", status="ไม่ทราบ"),  # invalid
    ])
    state = await _commit_with_matching_state(content)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        with pytest.raises(cv_import.CropVarietyImportHasErrors):
            await cv_import.commit(AsyncMock(), content, preview_state=state)
    create_mock.assert_not_called()


async def test_item35_no_hard_delete_anywhere_in_the_module():
    import inspect
    src = inspect.getsource(cv_import)
    assert "repo.delete" not in src
    assert ".delete(" not in src


async def test_item36_existing_active_crop_is_never_updated():
    """Excel can only ever CREATE a crop — an existing (active) crop is
    reused as-is; its own row never triggers an update() call."""
    crop = _md("crop", "พริก", active=True)
    content = _xlsx([_row(crop="พริก", variety="พริกขี้หนู")])
    state = await _commit_with_matching_state(content, existing_crops=[crop])
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(existing_crops=[crop]), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        await cv_import.commit(AsyncMock(), content, preview_state=state)
    # update() was called for the NEW variety's active flag only if needed —
    # here status defaults active, so update() is never called at all, and
    # crucially create() is never called for "พริก" (it already exists).
    assert all(c.args[1].type != "crop" for c in create_mock.call_args_list)
