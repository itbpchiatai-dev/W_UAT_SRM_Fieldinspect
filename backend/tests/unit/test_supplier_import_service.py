"""Supplier Excel import (round 8-20A) — template, validation, atomicity.

DB-less, matching this repo's import-test convention (see
test_master_data_crop_variety_import_service.py): the repository is patched
with in-memory fakes and the real service/endpoint logic is exercised against
them, so no test touches a real database, creates a real Supplier, or needs a
migration.

Template assertions read the generated .xlsx back with the app's OWN reader
(services/excel_reader.read_first_sheet) plus a little raw-XML inspection for
things a reader can't see (the red example sheet, freeze/filter/widths).
"""
from __future__ import annotations

import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zipfile import ZipFile
from io import BytesIO

import pytest

from app.schemas.supplier_import import (
    SupplierImportPreviewState,
    SupplierImportPreviewStateRow,
)
from app.services import supplier_import as svc
from app.services.excel_reader import read_first_sheet
from app.services.excel_workbook import build_xlsx
from app.services.supplier_import_report import (
    PHASE_COMMIT,
    PHASE_PREVIEW,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_READY,
    build_supplier_import_result_workbook,
    map_row_status,
    result_filename,
)

_M = "app.services.supplier_import"


# --- fakes ------------------------------------------------------------------


def _supplier(code="SUP001", name="Supplier One", **kw):
    base = dict(
        id=uuid4(), code=code, name=name, tax_id=None, contact_name=None,
        contact_email=None, contact_phone=None, address=None, is_active=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeRepo:
    """In-memory stand-in for supplier_repository. Records every write so a
    test can assert both WHAT happened and that nothing happened at all."""

    def __init__(self, suppliers=(), out_of_scope_ids=()):
        self.suppliers = list(suppliers)
        self.out_of_scope_ids = set(out_of_scope_ids)
        self.created: list[Any] = []
        self.updated: list[tuple[Any, Any]] = []
        self.query_count = 0
        self.raise_integrity_on_create = False

    async def get_suppliers_by_codes(self, db, codes):
        self.query_count += 1
        wanted = {c.lower() for c in codes}
        return {s.code.lower(): s for s in self.suppliers if s.code.lower() in wanted}

    async def filter_supplier_ids_in_scope(self, db, ids, scope_conditions):
        self.query_count += 1
        return {i for i in ids if i not in self.out_of_scope_ids}

    async def list_suppliers_for_template(self, db, *, scope_conditions):
        self.query_count += 1
        return list(self.suppliers)

    async def create_supplier(self, db, payload):
        if self.raise_integrity_on_create:
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        s = _supplier(
            code=payload.code.strip().upper(), name=payload.name,
            tax_id=payload.tax_id, contact_name=payload.contact_name,
            contact_email=payload.contact_email, contact_phone=payload.contact_phone,
            address=payload.address,
        )
        self.suppliers.append(s)
        self.created.append(payload)
        return s

    async def update_supplier(self, db, supplier, payload):
        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(supplier, field, value)
        self.updated.append((supplier, payload))
        return supplier


from typing import Any  # noqa: E402  (after _FakeRepo for readability)


def _with_repo(repo):
    return patch(f"{_M}.repo", repo)


# --- workbook helpers -------------------------------------------------------


def _row(action=svc.ACTION_SAVE, code="SUP001", name="Supplier One", tax_id="",
         contact_name="", email="", phone="", address="", status=svc.STATUS_ACTIVE):
    return [action, code, name, tax_id, contact_name, email, phone, address, status]


def _upload(rows, *, headers=None, include_description=True):
    """Build an uploadable workbook the way a user's edited template looks."""
    sheet = [list(headers or svc.IMPORT_COLUMNS)]
    if include_description:
        sheet.append([svc.TEMPLATE_COLUMN_DESCRIPTIONS[c] for c in svc.IMPORT_COLUMNS])
    sheet.extend(rows)
    return build_xlsx([(svc.SHEET_NAME, sheet)])


async def _preview(content, repo, *, can_create=True, can_update=True, scope=None):
    with _with_repo(repo):
        return await svc.build_preview(
            AsyncMock(), content, scope_conditions=scope or [],
            can_create=can_create, can_update=can_update,
        )


async def _commit(content, repo, *, state=None, can_create=True, can_update=True, scope=None):
    with _with_repo(repo):
        return await svc.commit(
            AsyncMock(), content, preview_state=state, scope_conditions=scope or [],
            can_create=can_create, can_update=can_update,
        )


async def _preview_then_commit(content, repo, **kw):
    preview = await _preview(content, repo, **kw)
    return await _commit(content, repo, state=preview.preview_state, **kw)


# --- Part C: template contract ---------------------------------------------


async def test_template_headers_are_the_9_columns_in_order():
    repo = _FakeRepo()
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    headers, _rows = read_first_sheet(content)
    assert headers == [
        "action", "supplierCode", "supplierName", "taxId", "contactName",
        "contactEmail", "contactPhone", "address", "status",
    ]
    assert headers == svc.IMPORT_COLUMNS


async def test_template_row_2_is_the_thai_description_row_and_is_skipped():
    repo = _FakeRepo(suppliers=[_supplier()])
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    _headers, rows = read_first_sheet(content)
    row2 = next(raw for n, raw in rows if n == 2)
    assert row2["action"].startswith(svc.TEMPLATE_DESCRIPTION_MARKER)
    # And the parser drops exactly that row.
    data = await svc._load_data_rows(content)
    assert 2 not in [n for n, _ in data]


async def test_template_lists_existing_suppliers_from_row_3():
    repo = _FakeRepo(suppliers=[_supplier(code="SUP001"), _supplier(code="SUP002")])
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    data = await svc._load_data_rows(content)
    assert [n for n, _ in data] == [3, 4]
    assert [raw["supplierCode"] for _n, raw in data] == ["SUP001", "SUP002"]
    assert all(raw["action"] == svc.ACTION_SAVE for _n, raw in data)


async def test_template_includes_inactive_suppliers_so_they_can_be_reactivated():
    repo = _FakeRepo(suppliers=[_supplier(code="SUP009", is_active=False)])
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    data = await svc._load_data_rows(content)
    assert data[0][1]["status"] == svc.STATUS_INACTIVE


async def test_template_has_a_second_example_sheet_marked_red():
    repo = _FakeRepo()
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    with ZipFile(BytesIO(content)) as zf:
        workbook = zf.read("xl/workbook.xml").decode()
        sheet2 = zf.read("xl/worksheets/sheet2.xml").decode()
        styles = zf.read("xl/styles.xml").decode()
    assert svc.SHEET_NAME in workbook
    assert svc.EXAMPLE_SHEET_NAME in workbook
    # The example sheet says so in words, and carries the red fill/font.
    assert svc._EXAMPLE_ONLY_NOTICE in sheet2
    assert "FFFFCDD2" in styles  # red background
    assert "FFB71C1C" in styles  # dark red font


async def test_the_example_sheet_is_never_the_parsed_sheet():
    """read_first_sheet always reads sheet1 — so the example rows can never be
    imported even though they look like valid data."""
    repo = _FakeRepo()
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    data = await svc._load_data_rows(content)
    codes = [raw.get("supplierCode") for _n, raw in data]
    assert "SUP999" not in codes  # the example-sheet code
    assert data == []  # no real suppliers → no data rows at all


async def test_template_freezes_the_header_filters_and_sets_column_widths():
    repo = _FakeRepo()
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    with ZipFile(BytesIO(content)) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode()
    assert 'state="frozen"' in sheet1
    assert "<autoFilter" in sheet1
    assert '<col min="1"' in sheet1
    assert 'customWidth="1"' in sheet1


async def test_template_status_column_has_a_closed_dropdown():
    repo = _FakeRepo()
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    with ZipFile(BytesIO(content)) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode()
    assert "dataValidation" in sheet1
    assert f"{svc.STATUS_ACTIVE},{svc.STATUS_INACTIVE}" in sheet1


async def test_template_preserves_leading_zeros_on_code_phone_and_tax_id():
    """The killer spreadsheet bug: a numeric cell renders 0812345678 as
    812345678. Every cell is written as an inline STRING, so it round-trips."""
    repo = _FakeRepo(suppliers=[_supplier(
        code="0012", tax_id="0105500000001", contact_phone="0812345678",
    )])
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    with ZipFile(BytesIO(content)) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode()
    assert 't="inlineStr"' in sheet1
    _headers, rows = read_first_sheet(content)
    row3 = next(raw for n, raw in rows if n == 3)
    assert row3["supplierCode"] == "0012"
    assert row3["taxId"] == "0105500000001"
    assert row3["contactPhone"] == "0812345678"


async def test_template_carries_no_uuid_or_internal_id():
    supplier = _supplier()
    repo = _FakeRepo(suppliers=[supplier])
    with _with_repo(repo):
        content = await svc.build_template(AsyncMock(), scope_conditions=[])
    with ZipFile(BytesIO(content)) as zf:
        blob = b"".join(zf.read(n) for n in zf.namelist()).decode(errors="ignore")
    assert str(supplier.id) not in blob
    assert supplier.id.hex not in blob


async def test_template_is_scope_filtered_via_the_repository():
    repo = _FakeRepo()
    scope = ["SENTINEL"]
    with _with_repo(repo), patch.object(
        repo, "list_suppliers_for_template", AsyncMock(return_value=[]),
    ) as mocked:
        await svc.build_template(AsyncMock(), scope_conditions=scope)
    assert mocked.await_args.kwargs["scope_conditions"] is scope


# --- Part E: per-row validation --------------------------------------------


async def test_create_row_for_an_unknown_code():
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100")]), repo)
    assert preview.rows[0].operation == svc.OPERATION_CREATE
    assert preview.rows[0].row_status == svc.ROW_STATUS_READY
    assert preview.summary.suppliers_to_create == 1


async def test_update_row_for_an_existing_code():
    repo = _FakeRepo(suppliers=[_supplier(code="SUP001", name="Old Name")])
    preview = await _preview(_upload([_row(code="SUP001", name="New Name")]), repo)
    assert preview.rows[0].operation == svc.OPERATION_UPDATE
    assert preview.summary.suppliers_to_update == 1


async def test_existing_code_matches_case_insensitively():
    repo = _FakeRepo(suppliers=[_supplier(code="SUP001", name="Same")])
    preview = await _preview(_upload([_row(code="sup001", name="Same")]), repo)
    # Resolved to the existing Supplier — not treated as a new code.
    assert preview.rows[0].operation == svc.OPERATION_NO_CHANGE


async def test_identical_row_is_no_change():
    repo = _FakeRepo(suppliers=[_supplier(
        code="SUP001", name="Supplier One", tax_id="123", contact_name="A",
        contact_email="a@b.co", contact_phone="0812345678", address="Addr",
    )])
    preview = await _preview(_upload([_row(
        code="SUP001", name="Supplier One", tax_id="123", contact_name="A",
        email="a@b.co", phone="0812345678", address="Addr",
    )]), repo)
    assert preview.rows[0].operation == svc.OPERATION_NO_CHANGE
    assert preview.summary.unchanged_rows == 1
    assert preview.summary.suppliers_to_update == 0


async def test_active_to_inactive_is_an_update_with_the_no_cascade_warning():
    repo = _FakeRepo(suppliers=[_supplier(code="SUP001", is_active=True)])
    preview = await _preview(
        _upload([_row(code="SUP001", status=svc.STATUS_INACTIVE)]), repo,
    )
    row = preview.rows[0]
    assert row.operation == svc.OPERATION_UPDATE
    assert row.row_status == svc.ROW_STATUS_READY  # a warning, never an error
    assert row.warning_message == svc.DEACTIVATE_WARNING
    assert "ไม่ถูกลบ" in row.warning_message
    assert preview.summary.suppliers_to_deactivate == 1


async def test_inactive_to_active_is_an_update_with_no_warning():
    repo = _FakeRepo(suppliers=[_supplier(code="SUP001", is_active=False)])
    preview = await _preview(
        _upload([_row(code="SUP001", status=svc.STATUS_ACTIVE)]), repo,
    )
    assert preview.rows[0].operation == svc.OPERATION_UPDATE
    assert preview.rows[0].warning_message == ""
    assert preview.summary.suppliers_to_activate == 1


async def test_blank_optional_on_an_update_clears_the_stored_value():
    existing = _supplier(
        code="SUP001", name="Supplier One", tax_id="123", contact_name="A",
        contact_email="a@b.co", contact_phone="0812345678", address="Addr",
    )
    repo = _FakeRepo(suppliers=[existing])
    content = _upload([_row(code="SUP001", name="Supplier One")])  # all optionals blank
    preview = await _preview(content, repo)
    assert preview.rows[0].operation == svc.OPERATION_UPDATE

    await _commit(content, repo, state=preview.preview_state)
    assert existing.tax_id is None
    assert existing.contact_name is None
    assert existing.contact_email is None
    assert existing.contact_phone is None
    assert existing.address is None
    assert existing.name == "Supplier One"  # required field untouched


@pytest.mark.parametrize("bad_action", ["", "delete_supplier", "SAVE_SUPPLIER", "create"])
async def test_only_save_supplier_is_an_accepted_action(bad_action):
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(action=bad_action, code="SUP100")]), repo)
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert svc.ACTION_SAVE in preview.rows[0].error_message


async def test_missing_supplier_code_is_an_error():
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="")]), repo)
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert "supplierCode" in preview.rows[0].error_message


async def test_missing_supplier_name_is_an_error():
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100", name="")]), repo)
    assert "supplierName" in preview.rows[0].error_message


@pytest.mark.parametrize(
    "field,value,needle",
    [
        ("code", "X" * 51, "supplierCode"),
        ("name", "X" * 256, "supplierName"),
        ("tax_id", "X" * 21, "taxId"),
        ("contact_name", "X" * 256, "contactName"),
        ("email", "a" * 260 + "@b.co", "contactEmail"),
        ("phone", "X" * 51, "contactPhone"),
    ],
)
async def test_over_length_values_are_errors(field, value, needle):
    repo = _FakeRepo()
    kwargs = {"code": "SUP100", field: value} if field != "code" else {"code": value}
    preview = await _preview(_upload([_row(**kwargs)]), repo)
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert needle in preview.rows[0].error_message


@pytest.mark.parametrize("bad", ["nope", "a@", "@b.co", "a b@c.co", "a@b", "a@@b.co"])
async def test_invalid_email_is_an_error(bad):
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100", email=bad)]), repo)
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert "contactEmail" in preview.rows[0].error_message


@pytest.mark.parametrize("good", ["a@b.co", "first.last@sub.example.com", "x+tag@y.io"])
async def test_valid_email_passes(good):
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100", email=good)]), repo)
    assert preview.rows[0].row_status == svc.ROW_STATUS_READY


async def test_blank_email_is_allowed():
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100", email="")]), repo)
    assert preview.rows[0].row_status == svc.ROW_STATUS_READY


@pytest.mark.parametrize("bad", ["", "ACTIVE", "เปิดใช้งาน", "enabled", "1"])
async def test_invalid_status_is_an_error(bad):
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100", status=bad)]), repo)
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert "status" in preview.rows[0].error_message


async def test_values_are_trimmed():
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="  SUP100  ", name="  Name  ")]), repo)
    assert preview.rows[0].supplier_code == "SUP100"
    assert preview.rows[0].supplier_name == "Name"


async def test_duplicate_code_in_the_file_flags_every_affected_row():
    repo = _FakeRepo()
    preview = await _preview(
        _upload([_row(code="SUP100"), _row(code="SUP100"), _row(code="SUP200")]), repo,
    )
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert preview.rows[1].row_status == svc.ROW_STATUS_ERROR
    assert preview.rows[2].row_status == svc.ROW_STATUS_READY
    assert "ซ้ำ" in preview.rows[0].error_message


async def test_duplicate_code_detection_is_case_insensitive():
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100"), _row(code="sup100")]), repo)
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert preview.rows[1].row_status == svc.ROW_STATUS_ERROR


async def test_missing_header_is_a_file_error():
    repo = _FakeRepo()
    headers = [c for c in svc.IMPORT_COLUMNS if c != "status"]
    sheet = [headers, [_row()[i] for i, c in enumerate(svc.IMPORT_COLUMNS) if c != "status"]]
    content = build_xlsx([(svc.SHEET_NAME, sheet)])
    with pytest.raises(svc.SupplierImportFileError) as exc:
        await _preview(content, repo)
    assert "status" in str(exc.value)


async def test_a_non_xlsx_payload_is_a_file_error():
    repo = _FakeRepo()
    with pytest.raises(svc.SupplierImportFileError):
        await _preview(b"not a workbook", repo)


async def test_too_many_rows_is_a_file_error():
    repo = _FakeRepo()
    rows = [_row(code=f"SUP{i:05d}") for i in range(svc.MAX_IMPORT_ROWS + 1)]
    with pytest.raises(svc.SupplierImportFileError):
        await _preview(_upload(rows), repo)


# --- Part D: per-row permissions -------------------------------------------


async def test_create_row_without_suppliers_create_is_an_error():
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100")]), repo, can_create=False)
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert "suppliers.create" in preview.rows[0].error_message


async def test_update_row_without_suppliers_update_is_an_error():
    repo = _FakeRepo(suppliers=[_supplier(code="SUP001", name="Old")])
    preview = await _preview(
        _upload([_row(code="SUP001", name="New")]), repo, can_update=False,
    )
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    assert "suppliers.update" in preview.rows[0].error_message


async def test_no_change_row_needs_no_write_permission():
    """A row that changes nothing writes nothing, so it must not demand
    suppliers.update — otherwise re-uploading an untouched template would
    fail for a read-mostly caller."""
    repo = _FakeRepo(suppliers=[_supplier(code="SUP001", name="Supplier One")])
    preview = await _preview(
        _upload([_row(code="SUP001", name="Supplier One")]), repo,
        can_create=False, can_update=False,
    )
    assert preview.rows[0].row_status == svc.ROW_STATUS_READY
    assert preview.rows[0].operation == svc.OPERATION_NO_CHANGE


async def test_a_permission_error_row_blocks_the_whole_commit():
    """A caller without suppliers.create runs the whole flow: the create row
    errors in Preview, and Commit refuses the file outright."""
    repo = _FakeRepo()
    content = _upload([_row(code="SUP100")])
    preview = await _preview(content, repo, can_create=False)
    assert preview.rows[0].row_status == svc.ROW_STATUS_ERROR
    with pytest.raises(svc.SupplierImportHasErrors):
        await _commit(content, repo, state=preview.preview_state, can_create=False)
    assert repo.created == []


async def test_permissions_changing_between_preview_and_commit_is_a_conflict():
    """Losing suppliers.create after approving a preview must not silently
    fall through to a partial import — the plans no longer match."""
    repo = _FakeRepo()
    content = _upload([_row(code="SUP100")])
    preview = await _preview(content, repo, can_create=True)
    with pytest.raises(svc.SupplierImportStateConflict):
        await _commit(content, repo, state=preview.preview_state, can_create=False)
    assert repo.created == []


# --- Part D: scope ----------------------------------------------------------


async def test_an_out_of_scope_existing_supplier_is_a_generic_row_error():
    hidden = _supplier(code="SUP001", name="Someone Else", contact_email="secret@x.co")
    repo = _FakeRepo(suppliers=[hidden], out_of_scope_ids=[hidden.id])
    preview = await _preview(_upload([_row(code="SUP001", name="Mine")]), repo)
    row = preview.rows[0]
    assert row.row_status == svc.ROW_STATUS_ERROR
    assert "ไม่มีสิทธิ์เข้าถึง" in row.error_message
    # Never describes the hidden Supplier.
    assert "Someone Else" not in row.error_message
    assert "secret@x.co" not in row.error_message


async def test_an_out_of_scope_supplier_is_never_written():
    hidden = _supplier(code="SUP001", name="Someone Else")
    repo = _FakeRepo(suppliers=[hidden], out_of_scope_ids=[hidden.id])
    content = _upload([_row(code="SUP001", name="Mine")])
    preview = await _preview(content, repo)
    with pytest.raises(svc.SupplierImportHasErrors):
        await _commit(content, repo, state=preview.preview_state)
    assert repo.updated == []
    assert hidden.name == "Someone Else"


# --- Part E: preview is read-only ------------------------------------------


async def test_preview_never_writes():
    repo = _FakeRepo(suppliers=[_supplier(code="SUP001", name="Old")])
    await _preview(
        _upload([_row(code="SUP001", name="New"), _row(code="SUP100")]), repo,
    )
    assert repo.created == []
    assert repo.updated == []


async def test_preview_resolves_all_rows_with_a_bounded_number_of_queries():
    """No N+1: one code lookup + one scope check for the WHOLE file, however
    many rows it has."""
    repo = _FakeRepo(suppliers=[_supplier(code=f"SUP{i:03d}") for i in range(50)])
    await _preview(_upload([_row(code=f"SUP{i:03d}") for i in range(50)]), repo)
    assert repo.query_count == 2


# --- Part E: commit atomicity ----------------------------------------------


async def test_commit_applies_create_and_update_together():
    existing = _supplier(code="SUP001", name="Old")
    repo = _FakeRepo(suppliers=[existing])
    content = _upload([_row(code="SUP001", name="New"), _row(code="SUP100", name="Brand New")])
    result = await _preview_then_commit(content, repo)
    assert result.created_suppliers == 1
    assert result.updated_suppliers == 1
    assert result.total_rows == 2
    assert existing.name == "New"


async def test_commit_creates_an_inactive_supplier_when_asked():
    repo = _FakeRepo()
    content = _upload([_row(code="SUP100", status=svc.STATUS_INACTIVE)])
    await _preview_then_commit(content, repo)
    assert repo.created and repo.suppliers[-1].is_active is False


async def test_one_bad_row_blocks_every_row():
    repo = _FakeRepo()
    content = _upload([_row(code="SUP100"), _row(code="SUP200", status="bogus")])
    preview = await _preview(content, repo)
    with pytest.raises(svc.SupplierImportHasErrors) as exc:
        await _commit(content, repo, state=preview.preview_state)
    assert exc.value.preview.summary.error_rows == 1
    # The GOOD row was not written either.
    assert repo.created == []
    assert repo.updated == []


async def test_commit_without_a_preview_state_is_a_conflict():
    repo = _FakeRepo()
    with pytest.raises(svc.SupplierImportStateConflict):
        await _commit(_upload([_row(code="SUP100")]), repo, state=None)
    assert repo.created == []


async def test_a_different_file_than_the_previewed_one_is_a_conflict():
    repo = _FakeRepo()
    preview = await _preview(_upload([_row(code="SUP100")]), repo)
    other = _upload([_row(code="SUP200")])
    with pytest.raises(svc.SupplierImportStateConflict):
        await _commit(other, repo, state=preview.preview_state)
    assert repo.created == []


async def test_a_supplier_edited_between_preview_and_commit_is_a_conflict():
    """The optimistic-concurrency binding: another admin changed the
    Supplier's data after this user approved the preview."""
    existing = _supplier(code="SUP001", name="Old")
    repo = _FakeRepo(suppliers=[existing])
    content = _upload([_row(code="SUP001", name="New")])
    preview = await _preview(content, repo)

    existing.contact_email = "changed@elsewhere.co"  # concurrent edit

    with pytest.raises(svc.SupplierImportStateConflict):
        await _commit(content, repo, state=preview.preview_state)
    assert repo.updated == []


async def test_a_supplier_created_between_preview_and_commit_is_a_conflict():
    repo = _FakeRepo()
    content = _upload([_row(code="SUP100", name="Mine")])
    preview = await _preview(content, repo)

    repo.suppliers.append(_supplier(code="SUP100", name="Someone else got there first"))

    with pytest.raises(svc.SupplierImportStateConflict):
        await _commit(content, repo, state=preview.preview_state)
    assert repo.created == []


async def test_a_tampered_preview_state_row_is_a_conflict():
    repo = _FakeRepo()
    content = _upload([_row(code="SUP100")])
    preview = await _preview(content, repo)
    tampered = SupplierImportPreviewState(
        file_sha256=preview.preview_state.file_sha256,
        rows=[SupplierImportPreviewStateRow(
            row_number=3, supplier_code="SUP100",
            operation=svc.OPERATION_UPDATE,  # claims update; the real plan is create
            supplier_existed=True, supplier_was_active=True,
        )],
    )
    with pytest.raises(svc.SupplierImportStateConflict):
        await _commit(content, repo, state=tampered)
    assert repo.created == []


async def test_integrity_error_propagates_so_the_transaction_rolls_back():
    """A unique-code race: the service never swallows it — the endpoint maps
    it to 409 and get_db rolls the whole transaction back."""
    from sqlalchemy.exc import IntegrityError

    repo = _FakeRepo()
    repo.raise_integrity_on_create = True
    content = _upload([_row(code="SUP100")])
    preview = await _preview(content, repo)
    with pytest.raises(IntegrityError):
        await _commit(content, repo, state=preview.preview_state)


async def test_a_non_integrity_error_also_propagates():
    repo = _FakeRepo()
    content = _upload([_row(code="SUP100")])
    preview = await _preview(content, repo)
    with patch.object(repo, "create_supplier", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError):
            await _commit(content, repo, state=preview.preview_state)


async def test_the_service_never_commits_or_rolls_back_the_session():
    """All-or-nothing lives in the caller's ONE transaction — the service must
    only ever flush (via the repository), never commit inside the row loop."""
    import inspect

    src = inspect.getsource(svc)
    assert "db.commit()" not in src
    assert "db.rollback()" not in src
    assert "session.commit()" not in src


async def test_commit_result_counts_every_category():
    active_to_inactive = _supplier(code="SUP001", is_active=True)
    inactive_to_active = _supplier(code="SUP002", is_active=False)
    unchanged = _supplier(code="SUP003", name="Supplier One", is_active=True)
    repo = _FakeRepo(suppliers=[active_to_inactive, inactive_to_active, unchanged])
    content = _upload([
        _row(code="SUP001", status=svc.STATUS_INACTIVE),
        _row(code="SUP002", status=svc.STATUS_ACTIVE),
        _row(code="SUP003", name="Supplier One"),
        _row(code="SUP100", name="Brand New"),
    ])
    result = await _preview_then_commit(content, repo)
    assert result.total_rows == 4
    assert result.created_suppliers == 1
    assert result.updated_suppliers == 2
    assert result.deactivated_suppliers == 1
    assert result.activated_suppliers == 1
    assert result.unchanged_rows == 1
    assert result.error_rows == 0
    assert [r.row_number for r in result.processed_rows] == [3, 4, 5, 6]
    assert {r.status for r in result.processed_rows} == {"COMPLETED", "NO_CHANGE"}


async def test_deactivation_touches_only_is_active_and_never_cascades():
    """No Plot/PlotCycle/Record is imported, referenced or written by this
    module — deactivating a Supplier is a single-field update."""
    import inspect

    supplier = _supplier(code="SUP001", is_active=True)
    repo = _FakeRepo(suppliers=[supplier])
    content = _upload([_row(code="SUP001", status=svc.STATUS_INACTIVE)])
    await _preview_then_commit(content, repo)
    assert supplier.is_active is False

    src = inspect.getsource(svc)
    # Structural, not prose: the module never imports any of those models,
    # so it has nothing to cascade to. (The docstring legitimately names them
    # when explaining that it does NOT touch them, so a raw substring grep
    # would self-trip.)
    for forbidden_import in (
        "from app.db.models.plot", "from app.db.models.plot_cycle",
        "from app.db.models.record", "plot_repository", "plot_cycle_repository",
        "record_repository",
    ):
        assert forbidden_import not in src


async def test_no_hard_delete_path_exists():
    import inspect

    src = inspect.getsource(svc)
    # Code-level checks only — the docstring explains the no-delete rule in
    # prose, so an uppercase "DELETE" grep would match its own documentation.
    assert "db.delete" not in src
    assert "repo.delete" not in src
    assert ".delete(" not in src


# --- Part E: result workbook -----------------------------------------------


def _view(row_status=svc.ROW_STATUS_READY, **kw):
    base = {
        "row_number": 3,
        "raw": {c: "" for c in svc.IMPORT_COLUMNS},
        "row_status": row_status,
        "operation": svc.OPERATION_CREATE,
        "error_message": "",
        "warning_message": "",
    }
    base.update(kw)
    return base


def test_result_workbook_appends_import_status_and_message_columns():
    content = build_supplier_import_result_workbook([_view()], phase=PHASE_PREVIEW)
    with ZipFile(BytesIO(content)) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode()
    assert "importStatus" in sheet1
    assert "importMessage" in sheet1
    assert "rowNumber" in sheet1
    for col in svc.IMPORT_COLUMNS:
        assert col in sheet1


def test_preview_ok_row_is_ready():
    assert map_row_status(_view(), completed=False) == STATUS_READY


def test_preview_bad_row_is_error_with_the_reason_and_row_number():
    view = _view(row_status=svc.ROW_STATUS_ERROR, error_message="status ไม่ถูกต้อง", row_number=7)
    assert map_row_status(view, completed=False) == STATUS_ERROR
    content = build_supplier_import_result_workbook([view], phase=PHASE_PREVIEW)
    with ZipFile(BytesIO(content)) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode()
    assert "status ไม่ถูกต้อง" in sheet1
    assert "<v>7</v>" in sheet1


def test_committed_row_is_completed():
    assert map_row_status(_view(), completed=True) == STATUS_COMPLETED


def test_the_deactivation_warning_reaches_the_preview_workbook():
    view = _view(warning_message=svc.DEACTIVATE_WARNING)
    content = build_supplier_import_result_workbook([view], phase=PHASE_PREVIEW)
    with ZipFile(BytesIO(content)) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode()
    assert "ไม่ถูกลบ" in sheet1


def test_result_workbook_neutralizes_formula_injection():
    view = _view(raw={**{c: "" for c in svc.IMPORT_COLUMNS}, "supplierName": "=cmd|'/c calc'!A1"})
    content = build_supplier_import_result_workbook([view], phase=PHASE_PREVIEW)
    with ZipFile(BytesIO(content)) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode()
    assert "&#39;=cmd" in sheet1 or "'=cmd" in sheet1


def test_result_workbook_never_carries_a_stack_trace():
    view = _view(row_status=svc.ROW_STATUS_ERROR, error_message="status ไม่ถูกต้อง")
    content = build_supplier_import_result_workbook([view], phase=PHASE_COMMIT, completed=True)
    with ZipFile(BytesIO(content)) as zf:
        blob = b"".join(zf.read(n) for n in zf.namelist()).decode(errors="ignore")
    for needle in ("Traceback", "File \"", ".py\", line", "sqlalchemy."):
        assert needle not in blob


def test_result_filename_is_server_generated_per_phase():
    at = datetime.datetime(2026, 8, 13, 10, 30, 0)
    assert result_filename(PHASE_PREVIEW, at) == "supplier-import-validation-20260813-103000.xlsx"
    assert result_filename(PHASE_COMMIT, at) == "supplier-import-result-20260813-103000.xlsx"


# --- schema/service constant sync (mirrors the master-data importer) -------


def test_schema_literals_match_the_service_constants():
    """schemas/supplier_import.py duplicates these as literals to stay
    dependency-free; this is the CI guard that they never drift."""
    from app.schemas.supplier_import import SupplierImportPreviewStateRow as Row

    assert svc.MAX_CODE_LEN == 50
    assert Row.model_fields["supplier_code"].metadata[0].max_length == svc.MAX_CODE_LEN
    assert set(svc.STATUS_VALUES) == {"active", "inactive"}
    assert {svc.OPERATION_CREATE, svc.OPERATION_UPDATE, svc.OPERATION_NO_CHANGE} == {
        "create", "update", "no_change",
    }


def test_preview_state_row_json_round_trips():
    row = SupplierImportPreviewStateRow(
        row_number=3, supplier_code="SUP001", operation=svc.OPERATION_UPDATE,
        supplier_existed=True, supplier_was_active=True,
        existing_state_digest="a" * 64,
    )
    state = SupplierImportPreviewState(file_sha256="b" * 64, rows=[row])
    reparsed = SupplierImportPreviewState.model_validate_json(
        json.dumps(state.model_dump(by_alias=True, mode="json"))
    )
    assert reparsed == state
