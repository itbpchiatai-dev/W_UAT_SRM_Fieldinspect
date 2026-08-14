"""Round 8-21A — PlotCycle Oracle Supplier Code / Oracle Invoice / Ref Account
reference fields: migration 0050, model, API schemas/repository, and the
Excel Plot Import backend (template/preview/commit/result workbook).

Three independent, OPTIONAL, free-text fields on PlotCycle (never Plot, never
Record, never Public Inspect). No business logic of their own — unlike
lot_no/po_number/p_code they never feed the Auto Lot formula; unlike
cycle_label they are never required.

Complements:
  - test_plot_cycle_po_lot_migration.py   (migration 0042, same test shape)
  - test_supplier_lot_contract_8_12a.py   (supplier_lot_no, the closest prior
                                            precedent for an independent
                                            free-text cycle field)
  - test_plot_cycle_lot_resolution.py     (repository create/update mock-db style)
  - test_plot_import_service.py           (DB-free commit harness)
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.plots import (
    _EDITABLE_COLUMNS,
    _new_cycle_row_values,
    _reactivate_row_values,
    _template_example_rows,
)
from app.schemas.plot import PlotCycleCreate, PlotCycleRead, PlotCycleUpdate
from app.schemas.plot_import import PlotImportPreviewState, PlotImportRowPayload
from app.services.cycle_reference_fields import normalize_cycle_reference_text
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    ImportContext,
    TEMPLATE_COLUMN_DESCRIPTIONS,
    _parse_row,
    _validate_row,
    build_preview,
    commit_import,
)
from app.services import plot_import_report as R

_M = "app.services.plot_import"
_REPO = "app.repositories.plot_cycle_repository"


# ===========================================================================
# 1. Migration 0050 — revision chain, nullable columns, no default/backfill
# ===========================================================================

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_08_13_0000-0050_plot_cycle_oracle_references.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_migration_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0050_plot_cycle_oracle_refs"
    assert down == "0049_auto_lot_v2_integrity"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_migration_adds_three_nullable_varchar255_columns() -> None:
    up = _upgrade()
    assert "ADD COLUMN oracle_supplier_code VARCHAR(255)" in up
    assert "ADD COLUMN oracle_invoice VARCHAR(255)" in up
    assert "ADD COLUMN ref_account VARCHAR(255)" in up
    # Nullable — no column-level NOT NULL, no default, no backfill.
    assert "NOT NULL" not in up
    assert "DEFAULT" not in up
    assert "UPDATE " not in up
    assert "INSERT INTO" not in up
    assert "DELETE " not in up


def test_migration_adds_no_constraint_no_index_no_rls() -> None:
    up = _upgrade()
    for token in (
        "CONSTRAINT", "CREATE INDEX", "CREATE UNIQUE INDEX",
        "ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY",
        "CREATE POLICY", "DROP POLICY", "ALTER POLICY", "GRANT ", "REVOKE ",
        "OWNER TO",
    ):
        assert token not in up, f"unexpected statement: {token}"
    # Touches only plot_cycles.
    for other in ("records", "plots ", "plot_access_phones", "suppliers"):
        assert other not in up, f"migration should not touch {other!r}"


def test_migration_downgrade_drops_exactly_the_three_columns() -> None:
    down = _downgrade()
    for col in ("ref_account", "oracle_invoice", "oracle_supplier_code"):
        assert f"DROP COLUMN IF EXISTS {col}" in down
    assert "DROP TABLE" not in down


def test_model_metadata_matches_migration() -> None:
    from app.db.models.plot_cycle import PlotCycle

    cols = PlotCycle.__table__.c
    for name in ("oracle_supplier_code", "oracle_invoice", "ref_account"):
        assert name in cols, f"model missing {name}"
        assert cols[name].nullable is True, f"{name} must be nullable"
        assert "VARCHAR" in str(cols[name].type).upper()
        assert "(255)" in str(cols[name].type)
        assert cols[name].default is None
        assert cols[name].server_default is None


# ===========================================================================
# 2. normalize_cycle_reference_text — shared trim/blank->None rule
# ===========================================================================

@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
def test_normalize_blank_becomes_none(blank) -> None:
    assert normalize_cycle_reference_text(blank) is None


def test_normalize_trims_surrounding_whitespace() -> None:
    assert normalize_cycle_reference_text("  ORC-001  ") == "ORC-001"


def test_normalize_preserves_case_and_internal_content() -> None:
    assert normalize_cycle_reference_text("Inv-2026-Q3") == "Inv-2026-Q3"


# ===========================================================================
# 3. API schemas — PlotCycleCreate/Update/Read camelCase round-trip
# ===========================================================================

def test_create_accepts_and_trims_all_three_fields() -> None:
    payload = PlotCycleCreate(
        pCode="WM-141", cycleLabel="jun2026",
        oracleSupplierCode="  ORC-SUP-1  ", oracleInvoice="  INV-1  ", refAccount="  ACC-1  ",
    )
    assert payload.oracle_supplier_code == "ORC-SUP-1"
    assert payload.oracle_invoice == "INV-1"
    assert payload.ref_account == "ACC-1"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_create_blank_fields_become_none(blank) -> None:
    payload = PlotCycleCreate(
        pCode="WM-141", cycleLabel="jun2026",
        oracleSupplierCode=blank, oracleInvoice=blank, refAccount=blank,
    )
    assert payload.oracle_supplier_code is None
    assert payload.oracle_invoice is None
    assert payload.ref_account is None


def test_create_fields_are_optional_and_default_none() -> None:
    payload = PlotCycleCreate(pCode="WM-141", cycleLabel="jun2026")
    assert payload.oracle_supplier_code is None
    assert payload.oracle_invoice is None
    assert payload.ref_account is None


@pytest.mark.parametrize("field", ["oracleSupplierCode", "oracleInvoice", "refAccount"])
def test_create_max_length_255_is_enforced(field: str) -> None:
    with pytest.raises(ValidationError):
        PlotCycleCreate(pCode="P", cycleLabel="jun2026", **{field: "X" * 256})


@pytest.mark.parametrize("field", ["oracleSupplierCode", "oracleInvoice", "refAccount"])
def test_create_exactly_255_chars_is_accepted(field: str) -> None:
    payload = PlotCycleCreate(pCode="P", cycleLabel="jun2026", **{field: "X" * 255})
    assert getattr(payload, {
        "oracleSupplierCode": "oracle_supplier_code",
        "oracleInvoice": "oracle_invoice",
        "refAccount": "ref_account",
    }[field]) == "X" * 255


def test_update_round_trips_camel_case() -> None:
    payload = PlotCycleUpdate(oracleSupplierCode=" ORC-9 ", oracleInvoice=" INV-9 ", refAccount=" ACC-9 ")
    assert payload.oracle_supplier_code == "ORC-9"
    assert payload.oracle_invoice == "INV-9"
    assert payload.ref_account == "ACC-9"
    dumped = payload.model_dump(by_alias=True, exclude_unset=True)
    assert dumped == {"oracleSupplierCode": "ORC-9", "oracleInvoice": "INV-9", "refAccount": "ACC-9"}


def test_update_omitting_fields_keeps_them_out_of_exclude_unset() -> None:
    """ABSENT must mean 'leave it alone' — the repository only writes a field
    when its key is present."""
    payload = PlotCycleUpdate(crop="พริก")
    dumped = payload.model_dump(exclude_unset=True)
    assert "oracle_supplier_code" not in dumped
    assert "oracle_invoice" not in dumped
    assert "ref_account" not in dumped


def test_update_explicit_null_is_present_in_exclude_unset() -> None:
    """Explicit null must reach the repository so it can CLEAR the value."""
    payload = PlotCycleUpdate(oracleSupplierCode=None, oracleInvoice=None, refAccount=None)
    dumped = payload.model_dump(exclude_unset=True)
    assert "oracle_supplier_code" in dumped
    assert "oracle_invoice" in dumped
    assert "ref_account" in dumped
    assert dumped["oracle_supplier_code"] is None


def test_update_blank_string_is_present_and_normalizes_to_none() -> None:
    payload = PlotCycleUpdate(oracleSupplierCode="   ")
    dumped = payload.model_dump(exclude_unset=True)
    assert "oracle_supplier_code" in dumped
    assert dumped["oracle_supplier_code"] is None


def test_read_model_exposes_all_three_camel_case() -> None:
    read = PlotCycleRead(
        id=uuid4(), plotId=uuid4(), cycleNo=1, status="active",
        crop=None, variety=None, cycleLabel="2605", lotNo="LOT-1",
        oracleSupplierCode="ORC-SUP-1", oracleInvoice="INV-1", refAccount="ACC-1",
        plantingDate=None, plantCount=None,
        expectedYieldFull=None, expectedYieldUnit=None,
        startedAt=datetime.datetime.now(datetime.timezone.utc),
        closedAt=None, closedById=None, closeReason=None,
        createdAt=datetime.datetime.now(datetime.timezone.utc),
        updatedAt=datetime.datetime.now(datetime.timezone.utc),
    )
    dumped = read.model_dump(by_alias=True)
    assert dumped["oracleSupplierCode"] == "ORC-SUP-1"
    assert dumped["oracleInvoice"] == "INV-1"
    assert dumped["refAccount"] == "ACC-1"


def test_read_model_defaults_all_three_to_none() -> None:
    """Cycles predating migration 0050 (no backfill) must still serialize."""
    read = PlotCycleRead(
        id=uuid4(), plotId=uuid4(), cycleNo=1, status="active",
        crop=None, variety=None, cycleLabel="2605", lotNo=None,
        plantingDate=None, plantCount=None,
        expectedYieldFull=None, expectedYieldUnit=None,
        startedAt=datetime.datetime.now(datetime.timezone.utc),
        closedAt=None, closedById=None, closeReason=None,
        createdAt=datetime.datetime.now(datetime.timezone.utc),
        updatedAt=datetime.datetime.now(datetime.timezone.utc),
    )
    assert read.oracle_supplier_code is None
    assert read.oracle_invoice is None
    assert read.ref_account is None


def test_fields_never_leak_into_record_or_public_inspect_schemas() -> None:
    """Cycle-level admin data only — never on Record or Public Inspect."""
    import app.schemas.record as record_schemas

    record_source = Path(record_schemas.__file__).read_text(encoding="utf-8")
    for name in ("oracle_supplier_code", "oracle_invoice", "ref_account",
                 "oracleSupplierCode", "oracleInvoice", "refAccount"):
        assert name not in record_source

    import app.schemas.phone_access as phone_access_schemas

    phone_access_source = Path(phone_access_schemas.__file__).read_text(encoding="utf-8")
    for name in ("oracle_supplier_code", "oracle_invoice", "ref_account",
                 "oracleSupplierCode", "oracleInvoice", "refAccount"):
        assert name not in phone_access_source


# ===========================================================================
# 4. Repository — create_cycle (mock-db style, mirrors test_plot_cycle_lot_resolution.py)
# ===========================================================================

def _mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _plot(plot_code: str = "SUP010-P001"):
    return SimpleNamespace(id=uuid4(), plot_code=plot_code, supplier_id=uuid4())


def _patch_create_cycle_collaborators(*, running=1):
    return (
        patch(f"{_REPO}._next_cycle_no", AsyncMock(return_value=1)),
        patch(f"{_REPO}._next_lot_running_no", AsyncMock(return_value=running)),
        patch(f"{_REPO}._supplier_code_for_plot", AsyncMock(return_value="SUP010")),
        patch(f"{_REPO}.sync_plot_mirror_from_cycle", AsyncMock()),
    )


async def test_create_cycle_stores_all_three_reference_fields() -> None:
    from app.repositories import plot_cycle_repository as repo

    plot = _plot()
    db = _mock_db()
    p1, p2, p3, p4 = _patch_create_cycle_collaborators()
    with p1, p2, p3, p4:
        cycle = await repo.create_cycle(
            db, plot, cycle_label="2605", p_code="WM-141",
            oracle_supplier_code="  ORC-SUP-1  ", oracle_invoice="  INV-1  ",
            ref_account="  ACC-1  ",
        )
    assert cycle.oracle_supplier_code == "ORC-SUP-1"
    assert cycle.oracle_invoice == "INV-1"
    assert cycle.ref_account == "ACC-1"
    # Never influences the lot decision.
    assert cycle.lot_no_source == "auto"


async def test_create_cycle_without_reference_fields_is_all_none() -> None:
    from app.repositories import plot_cycle_repository as repo

    plot = _plot()
    db = _mock_db()
    p1, p2, p3, p4 = _patch_create_cycle_collaborators()
    with p1, p2, p3, p4:
        cycle = await repo.create_cycle(db, plot, cycle_label="2605", p_code="WM-141")
    assert cycle.oracle_supplier_code is None
    assert cycle.oracle_invoice is None
    assert cycle.ref_account is None


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_create_cycle_blank_reference_fields_are_none(blank) -> None:
    from app.repositories import plot_cycle_repository as repo

    plot = _plot()
    db = _mock_db()
    p1, p2, p3, p4 = _patch_create_cycle_collaborators()
    with p1, p2, p3, p4:
        cycle = await repo.create_cycle(
            db, plot, cycle_label="2605", p_code="WM-141",
            oracle_supplier_code=blank, oracle_invoice=blank, ref_account=blank,
        )
    assert cycle.oracle_supplier_code is None
    assert cycle.oracle_invoice is None
    assert cycle.ref_account is None


# ===========================================================================
# 5. Repository — update_cycle presence semantics
# ===========================================================================

def _active_cycle(**over):
    base = dict(
        id=uuid4(), plot_id=uuid4(), status="active",
        crop=None, variety=None, cycle_label=None,
        lot_no="OLD-LOT", lot_no_source="manual", lot_running_no=None,
        auto_lot_series_key=None,
        po_number=None, p_code=None, supplier_lot_no=None,
        oracle_supplier_code="EXISTING-ORC", oracle_invoice="EXISTING-INV",
        ref_account="EXISTING-ACC",
        planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_update_omitted_fields_leave_existing_values_untouched() -> None:
    from app.repositories import plot_cycle_repository as repo

    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle()
    with patch(f"{_REPO}._supplier_code_for_plot", AsyncMock(return_value="SUP010")):
        await repo.update_cycle(db, plot, cycle, {"crop": "พริก"})
    assert cycle.oracle_supplier_code == "EXISTING-ORC"
    assert cycle.oracle_invoice == "EXISTING-INV"
    assert cycle.ref_account == "EXISTING-ACC"


async def test_update_present_none_clears_existing_values() -> None:
    from app.repositories import plot_cycle_repository as repo

    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle()
    with patch(f"{_REPO}._supplier_code_for_plot", AsyncMock(return_value="SUP010")):
        await repo.update_cycle(
            db, plot, cycle,
            {"oracle_supplier_code": None, "oracle_invoice": None, "ref_account": None},
        )
    assert cycle.oracle_supplier_code is None
    assert cycle.oracle_invoice is None
    assert cycle.ref_account is None


async def test_update_present_text_trims_and_saves() -> None:
    from app.repositories import plot_cycle_repository as repo

    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle()
    with patch(f"{_REPO}._supplier_code_for_plot", AsyncMock(return_value="SUP010")):
        await repo.update_cycle(
            db, plot, cycle,
            {"oracle_supplier_code": "  NEW-ORC  ", "oracle_invoice": "  NEW-INV  ",
             "ref_account": "  NEW-ACC  "},
        )
    assert cycle.oracle_supplier_code == "NEW-ORC"
    assert cycle.oracle_invoice == "NEW-INV"
    assert cycle.ref_account == "NEW-ACC"


async def test_update_present_blank_string_clears() -> None:
    from app.repositories import plot_cycle_repository as repo

    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle()
    with patch(f"{_REPO}._supplier_code_for_plot", AsyncMock(return_value="SUP010")):
        await repo.update_cycle(db, plot, cycle, {"oracle_invoice": "   "})
    assert cycle.oracle_invoice is None
    # Fields not mentioned stay untouched.
    assert cycle.oracle_supplier_code == "EXISTING-ORC"
    assert cycle.ref_account == "EXISTING-ACC"


# ===========================================================================
# 6. Repository — rollover_cycle NEVER auto-copies the closing cycle's values
# ===========================================================================

async def test_rollover_does_not_copy_closing_cycles_reference_fields() -> None:
    from app.repositories import plot_cycle_repository as repo

    db = _mock_db()
    plot = _plot()
    current = _active_cycle(
        oracle_supplier_code="OLD-ORC", oracle_invoice="OLD-INV", ref_account="OLD-ACC",
    )
    p1, p2, p3, p4 = _patch_create_cycle_collaborators()
    with p1, p2, p3, p4, \
         patch(f"{_REPO}.close_cycle", AsyncMock(return_value=current)), \
         patch(f"{_REPO}.clear_plot_inspection_snapshot", AsyncMock()):
        _closed, new_cycle = await repo.rollover_cycle(
            db, plot, current,
            close_status="harvested", closed_by_id=None, close_reason=None,
            cycle_label="jul2026", p_code="WM-141",
            # Deliberately NOT passing oracle_supplier_code/oracle_invoice/ref_account.
        )
    assert new_cycle.oracle_supplier_code is None
    assert new_cycle.oracle_invoice is None
    assert new_cycle.ref_account is None


async def test_rollover_uses_explicitly_passed_values_for_the_new_cycle() -> None:
    from app.repositories import plot_cycle_repository as repo

    db = _mock_db()
    plot = _plot()
    current = _active_cycle(
        oracle_supplier_code="OLD-ORC", oracle_invoice="OLD-INV", ref_account="OLD-ACC",
    )
    p1, p2, p3, p4 = _patch_create_cycle_collaborators()
    with p1, p2, p3, p4, \
         patch(f"{_REPO}.close_cycle", AsyncMock(return_value=current)), \
         patch(f"{_REPO}.clear_plot_inspection_snapshot", AsyncMock()):
        _closed, new_cycle = await repo.rollover_cycle(
            db, plot, current,
            close_status="harvested", closed_by_id=None, close_reason=None,
            cycle_label="jul2026", p_code="WM-141",
            oracle_supplier_code="NEW-ORC", oracle_invoice="NEW-INV", ref_account="NEW-ACC",
        )
    assert new_cycle.oracle_supplier_code == "NEW-ORC"
    assert new_cycle.oracle_invoice == "NEW-INV"
    assert new_cycle.ref_account == "NEW-ACC"


# ===========================================================================
# 7. API endpoint source — every create-cycle/rollover call site forwards
#    the three fields (regression guard against a future edit dropping one).
# ===========================================================================

def test_plots_api_forwards_reference_fields_at_every_call_site() -> None:
    src = Path("app/api/v1/plots.py").read_text(encoding="utf-8")
    # 4 sites: create_plot_with_cycle, start_plot_cycle, rollover_plot_cycle,
    # reactivate_plot_with_cycle.
    assert src.count("oracle_supplier_code=") >= 4
    assert src.count("oracle_invoice=") >= 4
    assert src.count("ref_account=") >= 4


def test_plot_import_service_forwards_reference_fields_at_every_call_site() -> None:
    src = Path("app/services/plot_import.py").read_text(encoding="utf-8")
    # 5 create_cycle/rollover_cycle/reactivate_plot_with_cycle call sites
    # (create, start, rollover, start_next->create, start_next->rollover,
    # reactivate) + the update_current_cycle presence-aware fields dict.
    assert src.count("oracle_supplier_code=p.oracle_supplier_code") >= 5
    assert src.count("oracle_invoice=p.oracle_invoice") >= 5
    assert src.count("ref_account=p.ref_account") >= 5


# ===========================================================================
# 8. Excel — IMPORT_COLUMNS position, TEMPLATE_COLUMN_DESCRIPTIONS, payload
# ===========================================================================

def test_import_columns_include_the_three_new_columns_after_supplier_lot_no() -> None:
    idx = IMPORT_COLUMNS.index("supplierLotNo")
    assert IMPORT_COLUMNS[idx + 1] == "oracleSupplierCode"
    assert IMPORT_COLUMNS[idx + 2] == "oracleInvoice"
    assert IMPORT_COLUMNS[idx + 3] == "refAccount"


def test_template_descriptions_cover_all_three_columns() -> None:
    for col in ("oracleSupplierCode", "oracleInvoice", "refAccount"):
        assert col in TEMPLATE_COLUMN_DESCRIPTIONS
        assert TEMPLATE_COLUMN_DESCRIPTIONS[col].strip() != ""


def test_payload_schema_echoes_all_three_fields() -> None:
    for field in ("oracle_supplier_code", "oracle_invoice", "ref_account"):
        assert field in PlotImportRowPayload.model_fields


def test_editable_columns_include_the_three_new_columns() -> None:
    for col in ("oracleSupplierCode", "oracleInvoice", "refAccount"):
        assert col in _EDITABLE_COLUMNS


def test_example_rows_demonstrate_the_three_columns() -> None:
    examples = _template_example_rows("SUP001")
    create_example = next(e for e in examples if e["action"] == "create_plot_with_cycle")
    assert create_example["oracleSupplierCode"]
    assert create_example["oracleInvoice"]
    assert create_example["refAccount"]


# ===========================================================================
# 9. Excel — _parse_row column-presence semantics (the core new behaviour)
# ===========================================================================

def test_parse_row_column_absent_gives_none_and_given_false() -> None:
    """An older workbook with none of the three columns at all."""
    raw = {"action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P001"}
    p, errors = _parse_row(raw, columns_present=frozenset(raw.keys()))
    assert errors == []
    assert p.oracle_supplier_code is None
    assert p.oracle_supplier_code_given is False
    assert p.oracle_invoice_given is False
    assert p.ref_account_given is False


def test_parse_row_column_present_blank_gives_none_and_given_true() -> None:
    """The column exists in the workbook's header row, but this row's cell
    is blank (or absent because excel_reader omits blank cells) — the
    COLUMN's presence in the header set is what flips *_given, not the cell."""
    raw = {"action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P001"}
    columns = frozenset(raw.keys()) | {"oracleSupplierCode", "oracleInvoice", "refAccount"}
    p, errors = _parse_row(raw, columns_present=columns)
    assert errors == []
    assert p.oracle_supplier_code is None
    assert p.oracle_supplier_code_given is True
    assert p.oracle_invoice_given is True
    assert p.ref_account_given is True


def test_parse_row_column_present_with_text_trims_and_sets_given_true() -> None:
    raw = {
        "action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P001",
        "oracleSupplierCode": "  ORC-1  ", "oracleInvoice": "  INV-1  ", "refAccount": "  ACC-1  ",
    }
    p, errors = _parse_row(raw, columns_present=frozenset(raw.keys()))
    assert errors == []
    assert p.oracle_supplier_code == "ORC-1"
    assert p.oracle_supplier_code_given is True
    assert p.oracle_invoice == "INV-1"
    assert p.ref_account == "ACC-1"


def test_parse_row_default_columns_present_is_empty_never_marks_given() -> None:
    """The default parameter value must never accidentally mark a column as
    present when the caller omits it (only _validate_all's real columns_present,
    derived from the workbook's own header row, should)."""
    raw = {"action": "x", "oracleSupplierCode": "ORC-1"}
    p, _ = _parse_row(raw)
    assert p.oracle_supplier_code == "ORC-1"  # value still parses
    assert p.oracle_supplier_code_given is False  # but "given" needs columns_present


# ===========================================================================
# 10. Excel — length validation (255 cap, ERROR names the column + row number)
# ===========================================================================

def _supplier_stub(**kw):
    return SimpleNamespace(id=kw.get("id", uuid4()), code="SUP001", is_active=True)


@pytest.mark.parametrize("field,column", [
    ("oracle_supplier_code", "oracleSupplierCode"),
    ("oracle_invoice", "oracleInvoice"),
    ("ref_account", "refAccount"),
])
async def test_validate_row_over_255_chars_is_a_row_error_naming_the_column(field, column) -> None:
    raw = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001", "plotCode": "P999",
        "plotName": "แปลงทดสอบ", "cycleLabel": "jun2026", "pCode": "Melon-A",
        column: "X" * 256,
    }
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier_stub())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=None)):
        state = await _validate_row(object(), 3, raw, ImportContext(
            allowed_supplier_id=None, can_create=True, can_update=True,
        ), set())
    assert state.errors, "a 256-char value must fail validation"
    assert any(column in e for e in state.errors)
    assert state.row_number == 3


@pytest.mark.parametrize("column", ["oracleSupplierCode", "oracleInvoice", "refAccount"])
async def test_validate_row_exactly_255_chars_passes_length_check(column) -> None:
    raw = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001", "plotCode": "P999",
        "plotName": "แปลงทดสอบ", "cycleLabel": "jun2026", "pCode": "Melon-A",
        column: "X" * 255,
    }
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier_stub())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=None)):
        state = await _validate_row(object(), 5, raw, ImportContext(
            allowed_supplier_id=None, can_create=True, can_update=True,
        ), set())
    assert not any("255" in e for e in state.errors)


# ===========================================================================
# 11. Excel — end-to-end commit (DB-free, mirrors test_plot_import_service.py)
# ===========================================================================

def _xlsx(rows: list[dict[str, str]], columns: list[str] | None = None) -> bytes:
    cols = columns if columns is not None else IMPORT_COLUMNS
    data: list[list] = [list(cols)]
    for r in rows:
        data.append([r.get(c) for c in cols])
    return build_xlsx([("plots", data)])


def _ctx(*, allowed=None, can_create=True, can_update=True) -> ImportContext:
    return ImportContext(allowed_supplier_id=allowed, can_create=can_create, can_update=can_update)


def _cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, crop=None, variety=None, cycle_label=None,
        lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        oracle_supplier_code=None, oracle_invoice=None, ref_account=None,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _create_row(**over) -> dict[str, str]:
    base = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "P101", "plotName": "แปลงใหม่", "province": "เชียงใหม่",
        "poNumber": "PO25001", "pCode": "Melon-A", "cycleLabel": "jun2026",
        "crop": "พริก", "variety": "พริกขี้หนู", "lotNo": "LOT-01",
        "plantingDate": "2026-06-01", "plantCount": "1000",
        "expectedYieldFull": "800", "expectedYieldUnit": "kg",
    }
    base.update(over)
    return base


def _patch_lookups(*, supplier=..., plot=None, active=None):
    sup = _supplier_stub() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
    )


async def _preview(rows, ctx=None, columns=None, **lookups):
    ctx = ctx or _ctx()
    p_sup, p_plot, p_active = _patch_lookups(**lookups)
    with p_sup, p_plot, p_active:
        return await build_preview(object(), _xlsx(rows, columns), ctx=ctx)


async def test_preview_echoes_the_three_fields_when_given() -> None:
    pv = await _preview([_create_row(
        oracleSupplierCode=" ORC-1 ", oracleInvoice=" INV-1 ", refAccount=" ACC-1 ",
    )])
    row = pv.rows[0]
    assert row.status == "valid", row.message
    assert row.payload.oracle_supplier_code == "ORC-1"
    assert row.payload.oracle_invoice == "INV-1"
    assert row.payload.ref_account == "ACC-1"


async def test_commit_create_stores_all_three_fields() -> None:
    plot = SimpleNamespace(id=uuid4(), code="P101", is_active=True)
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle())) as m_create_cycle:
        await commit_import(object(), _xlsx([_create_row(
            oracleSupplierCode="ORC-1", oracleInvoice="INV-1", refAccount="ACC-1",
        )]), ctx=_ctx())

    m_create_cycle.assert_awaited_once()
    _, kwargs = m_create_cycle.call_args
    assert kwargs["oracle_supplier_code"] == "ORC-1"
    assert kwargs["oracle_invoice"] == "INV-1"
    assert kwargs["ref_account"] == "ACC-1"


async def test_commit_update_column_absent_omits_fields_preserves() -> None:
    """An OLD workbook with none of the three columns: update_cycle must not
    even receive the keys (preserve)."""
    plot = SimpleNamespace(id=uuid4(), code="P002", is_active=True)
    active = _cycle(oracle_supplier_code="EXISTING", oracle_invoice="EXISTING", ref_account="EXISTING")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    old_columns = [c for c in IMPORT_COLUMNS
                   if c not in ("oracleSupplierCode", "oracleInvoice", "refAccount")]
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as m_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await commit_import(
            object(),
            _xlsx([_create_row(action="update_current_cycle", plotCode="P002")], columns=old_columns),
            ctx=_ctx(),
        )

    m_update.assert_awaited_once()
    called_fields = m_update.call_args.args[-1]
    assert "oracle_supplier_code" not in called_fields
    assert "oracle_invoice" not in called_fields
    assert "ref_account" not in called_fields


async def test_commit_update_column_present_blank_clears() -> None:
    """A NEW workbook where the columns exist but this row leaves them blank
    — clears the stored value (DIFFERENT from poNumber/pCode/supplierLotNo)."""
    plot = SimpleNamespace(id=uuid4(), code="P002", is_active=True)
    active = _cycle(oracle_supplier_code="EXISTING", oracle_invoice="EXISTING", ref_account="EXISTING")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as m_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await commit_import(
            object(),
            _xlsx([_create_row(action="update_current_cycle", plotCode="P002",
                                oracleSupplierCode=None, oracleInvoice=None, refAccount=None)]),
            ctx=_ctx(),
        )

    m_update.assert_awaited_once()
    called_fields = m_update.call_args.args[-1]
    assert called_fields["oracle_supplier_code"] is None
    assert called_fields["oracle_invoice"] is None
    assert called_fields["ref_account"] is None


async def test_commit_update_column_present_with_text_sets() -> None:
    plot = SimpleNamespace(id=uuid4(), code="P002", is_active=True)
    active = _cycle(oracle_supplier_code="EXISTING", oracle_invoice="EXISTING", ref_account="EXISTING")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as m_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await commit_import(
            object(),
            _xlsx([_create_row(action="update_current_cycle", plotCode="P002",
                                oracleSupplierCode="NEW-ORC", oracleInvoice="NEW-INV",
                                refAccount="NEW-ACC")]),
            ctx=_ctx(),
        )

    m_update.assert_awaited_once()
    called_fields = m_update.call_args.args[-1]
    assert called_fields["oracle_supplier_code"] == "NEW-ORC"
    assert called_fields["oracle_invoice"] == "NEW-INV"
    assert called_fields["ref_account"] == "NEW-ACC"


async def test_old_workbook_without_the_three_columns_still_imports() -> None:
    """A file built entirely without oracleSupplierCode/oracleInvoice/refAccount
    (a pre-8-21A download) must still import a create row successfully."""
    old_columns = [c for c in IMPORT_COLUMNS
                   if c not in ("oracleSupplierCode", "oracleInvoice", "refAccount")]
    pv = await _preview([_create_row()], columns=old_columns)
    row = pv.rows[0]
    assert row.status == "valid", row.message
    assert row.payload.oracle_supplier_code is None
    assert row.payload.oracle_invoice is None
    assert row.payload.ref_account is None


# ===========================================================================
# 12. previewState — no per-row binding needed; the whole-file file_sha256
#     binding already covers these (no server-side race/computation, unlike
#     Auto Lot or start_next_cycle's resolved_action).
# ===========================================================================

def test_preview_state_has_no_dedicated_oracle_row_binding() -> None:
    """Unlike start_next_cycle/final_plot/credential rows (which bind a
    SERVER-COMPUTED resolution that can drift between Preview and Commit),
    oracleSupplierCode/oracleInvoice/refAccount are plain client-supplied text
    with no server computation — the existing file_sha256 whole-file binding
    is sufficient (commit re-parses the SAME bytes), so no new PlotImport*
    PreviewStateRow model was added for them."""
    assert "file_sha256" in PlotImportPreviewState.model_fields
    for name, field in PlotImportPreviewState.model_fields.items():
        if name == "file_sha256":
            continue
        # every other field is a list of per-row bindings — none of them
        # should be named after the oracle reference columns.
        assert "oracle" not in name.lower()
        assert "ref_account" not in name.lower()


def test_same_file_bytes_parse_identically_twice() -> None:
    """Determinism check backing the file_sha256 binding: parsing the exact
    same row dict for the same columns_present always yields the same
    oracle_* values and *_given flags — Preview and Commit (which both parse
    the identical uploaded bytes) can never disagree."""
    raw = {
        "action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P001",
        "oracleSupplierCode": "ORC-1",
    }
    columns = frozenset(raw.keys())
    p1, _ = _parse_row(raw, columns)
    p2, _ = _parse_row(raw, columns)
    assert (p1.oracle_supplier_code, p1.oracle_supplier_code_given) == \
           (p2.oracle_supplier_code, p2.oracle_supplier_code_given)


# ===========================================================================
# 13. Result workbook — column widths / headers include the 3 new columns
# ===========================================================================

def test_all_columns_includes_the_three_new_columns() -> None:
    assert "oracleSupplierCode" in R.ALL_COLUMNS
    assert "oracleInvoice" in R.ALL_COLUMNS
    assert "refAccount" in R.ALL_COLUMNS


def test_col_widths_length_matches_all_columns_length() -> None:
    assert len(R._COL_WIDTHS) == len(R.ALL_COLUMNS)


def test_result_workbook_raw_echo_includes_the_three_columns() -> None:
    view = {
        "row_number": 3, "action": "create_plot_with_cycle", "status": "valid",
        "message": "", "error_code": None, "result_cycle_no": 1,
        "raw": {"oracleSupplierCode": "ORC-1", "oracleInvoice": "INV-1", "refAccount": "ACC-1"},
    }
    content = R.build_plot_import_result_workbook(
        [view], phase=R.PHASE_PREVIEW, processed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    from io import BytesIO
    from zipfile import ZipFile
    with ZipFile(BytesIO(content)) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "ORC-1" in sheet1
    assert "INV-1" in sheet1
    assert "ACC-1" in sheet1


# ===========================================================================
# 14. Template row prefill — new_cycle / reactivate rows carry the active/
#     latest cycle's current values (filter-aware Template contract).
# ===========================================================================

def _plot_stub(*, is_active: bool, active_cycle=None):
    return SimpleNamespace(
        id=uuid4(), plot_code="P001", name="แปลงหนึ่ง", is_active=is_active,
        village=None, district=None, province="เชียงใหม่",
        latitude=None, longitude=None, rai=None,
        supplier=SimpleNamespace(code="SUP001", name="Supplier One", is_active=True),
        active_cycle=active_cycle, access_phones=[], cycles=[],
    )


def test_new_cycle_row_prefills_active_cycles_reference_fields() -> None:
    cycle = _cycle(
        oracle_supplier_code="ORC-ACTIVE", oracle_invoice="INV-ACTIVE", ref_account="ACC-ACTIVE",
    )
    plot = _plot_stub(is_active=True, active_cycle=cycle)
    values = _new_cycle_row_values(plot)
    assert values["oracleSupplierCode"] == "ORC-ACTIVE"
    assert values["oracleInvoice"] == "INV-ACTIVE"
    assert values["refAccount"] == "ACC-ACTIVE"


def test_new_cycle_row_blank_when_no_active_cycle() -> None:
    plot = _plot_stub(is_active=True, active_cycle=None)
    values = _new_cycle_row_values(plot)
    assert values["oracleSupplierCode"] is None
    assert values["oracleInvoice"] is None
    assert values["refAccount"] is None


def test_reactivate_row_prefills_latest_historical_cycles_reference_fields() -> None:
    latest = _cycle(
        oracle_supplier_code="ORC-HIST", oracle_invoice="INV-HIST", ref_account="ACC-HIST",
    )
    plot = _plot_stub(is_active=False)
    values = _reactivate_row_values(plot, latest)
    assert values["oracleSupplierCode"] == "ORC-HIST"
    assert values["oracleInvoice"] == "INV-HIST"
    assert values["refAccount"] == "ACC-HIST"


def test_reactivate_row_blank_when_no_cycle_history() -> None:
    plot = _plot_stub(is_active=False)
    values = _reactivate_row_values(plot, None)
    assert values["oracleSupplierCode"] is None
    assert values["oracleInvoice"] is None
    assert values["refAccount"] is None
