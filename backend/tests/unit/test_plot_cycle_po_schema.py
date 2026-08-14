"""Round 8-5A — PO / P.Code schema contract on PlotCycleCreate/Update/Read:
normalization at the API boundary, over-length → 422, and the SERVER-derived
lot_no_source / lot_running_no never being client-settable.

Round 8-13A — poNumber became OPTIONAL on PlotCycleCreate (it left the Auto
Lot V2 formula back in round 8-12A; this was the last place a new-cycle
request was still gated on it). pCode's requiredness is UNCHANGED."""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.plot import (
    PlotCreate,
    PlotCycleCreate,
    PlotCycleRead,
    PlotCycleRollover,
    PlotCycleUpdate,
    PlotWithCycleCreate,
)


# --- normalization ----------------------------------------------------------

def test_create_po_number_trimmed_and_uppercased() -> None:
    assert PlotCycleCreate(poNumber="  po25001 ", pCode="X", cycleLabel="jun2026").po_number == "PO25001"


def test_create_p_code_trimmed_case_preserved() -> None:
    assert PlotCycleCreate(poNumber="PO", pCode="  Melon-A ", cycleLabel="jun2026").p_code == "Melon-A"


# --- round 8-13A: poNumber is OPTIONAL on create; pCode stays REQUIRED ------

def test_create_omitted_po_number_is_valid_and_none() -> None:
    created = PlotCycleCreate(pCode="X", cycleLabel="jun2026")
    assert created.po_number is None


def test_create_explicit_null_po_number_is_valid_and_none() -> None:
    created = PlotCycleCreate(poNumber=None, pCode="X", cycleLabel="jun2026")
    assert created.po_number is None


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_create_blank_po_number_normalizes_to_none(blank: str) -> None:
    created = PlotCycleCreate(poNumber=blank, pCode="X", cycleLabel="jun2026")
    assert created.po_number is None


def test_create_missing_p_code_still_422() -> None:
    # pCode alone is still required — omitting it (even with a valid PO) 422s.
    # cycleLabel supplied here so this isolates the pCode rule specifically.
    with pytest.raises(ValidationError) as exc:
        PlotCycleCreate(poNumber="PO25001", cycleLabel="jun2026")
    missing = {e["loc"][0] for e in exc.value.errors()}
    assert "pCode" in missing
    assert "poNumber" not in missing  # PO is never reported as missing anymore


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_create_blank_p_code_still_rejected(blank: str) -> None:
    # pCode's own blank-rejection is unchanged by this round.
    with pytest.raises(ValidationError):
        PlotCycleCreate(poNumber="PO25001", pCode=blank)


def test_create_both_po_and_pcode_omitted_only_reports_pcode_missing() -> None:
    # cycleLabel supplied so this isolates po/pCode reporting specifically —
    # its OWN requirement is covered by test_plot_cycle_label_required.py.
    with pytest.raises(ValidationError) as exc:
        PlotCycleCreate(cycleLabel="jun2026")
    missing = {e["loc"][0] for e in exc.value.errors()}
    assert missing == {"pCode"}


def test_update_po_number_normalized() -> None:
    assert PlotCycleUpdate(poNumber="po-9").po_number == "PO-9"


# --- round 8-13A: PlotCycleUpdate preserve/clear semantics (unchanged, ------
# reconfirmed here since Create now shares the same normalize_po_number call)

def test_update_omitted_po_number_is_unset_not_none() -> None:
    # exclude_unset semantics live in the repository, not the schema — but the
    # schema must distinguish "omitted" from "explicit null" for that to work.
    updated = PlotCycleUpdate()
    assert "po_number" not in updated.model_fields_set


def test_update_explicit_null_po_number_is_set_to_none() -> None:
    updated = PlotCycleUpdate(poNumber=None)
    assert "po_number" in updated.model_fields_set
    assert updated.po_number is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_update_blank_po_number_normalizes_to_none(blank: str) -> None:
    updated = PlotCycleUpdate(poNumber=blank)
    assert "po_number" in updated.model_fields_set
    assert updated.po_number is None


# --- over-length → 422 (never silently truncated) ---------------------------

def test_create_po_number_over_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        PlotCycleCreate(poNumber="P" * 101, pCode="X", cycleLabel="jun2026")


def test_create_p_code_over_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        PlotCycleCreate(pCode="C" * 101, cycleLabel="jun2026")


def test_update_po_number_over_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        PlotCycleUpdate(poNumber="P" * 101)


# --- server-derived fields are NOT client-settable --------------------------

def test_create_and_update_reject_server_derived_lot_fields() -> None:
    # lot_no_source / lot_running_no are derived by the repository — they must
    # not be fields a client can supply on the write schemas.
    for model in (PlotCycleCreate, PlotCycleUpdate):
        assert "lot_no_source" not in model.model_fields
        assert "lot_running_no" not in model.model_fields
    # …and passing them is not honored (no such attribute is set).
    created = PlotCycleCreate(poNumber="PO", pCode="X", cycleLabel="jun2026", lotNoSource="auto", lotRunningNo=5)  # ignored
    assert not hasattr(created, "lot_no_source")
    assert not hasattr(created, "lot_running_no")


# --- read schema exposes all four -------------------------------------------

def test_read_schema_exposes_po_lot_metadata() -> None:
    for field in ("po_number", "p_code", "lot_no_source", "lot_running_no"):
        assert field in PlotCycleRead.model_fields


# --- round 8-13A: every wrapper that reuses PlotCycleCreate inherits the ----
# same optional-PO contract, with no duplicate validation of its own.

def test_rollover_new_cycle_without_po_is_valid() -> None:
    rollover = PlotCycleRollover(
        closeStatus="harvested",
        newCycle=PlotCycleCreate(pCode="X", cycleLabel="jun2026"),
    )
    assert rollover.new_cycle.po_number is None


def test_create_plot_with_cycle_without_po_is_valid() -> None:
    payload = PlotWithCycleCreate(
        plot=PlotCreate(supplierId=uuid4(), plotCode="P900", name="แปลงทดสอบ"),
        cycle=PlotCycleCreate(pCode="X", cycleLabel="jun2026"),
    )
    assert payload.cycle.po_number is None


def test_reactivate_with_cycle_payload_without_po_is_valid() -> None:
    # reactivate-with-cycle's endpoint body IS PlotCycleCreate directly (see
    # POST /plots/{id}/reactivate-with-cycle) — same contract, no wrapper.
    payload = PlotCycleCreate(pCode="X", cycleLabel="jun2026")
    assert payload.po_number is None
    assert payload.p_code == "X"


# --- round 8-13A: Auto Lot / Manual Lot both work with no PO at all ---------

def test_auto_lot_create_without_po_but_with_label_and_pcode_is_valid() -> None:
    # lotNo blank (Auto) + cycleLabel + pCode present + no PO — the schema
    # itself has nothing more to say about Auto Lot (that's the repository's
    # job, see test_plot_cycle_lot_resolution.py), but it must not 422 here.
    created = PlotCycleCreate(cycleLabel="2605", pCode="WM-141")
    assert created.po_number is None
    assert created.lot_no is None
    assert created.cycle_label == "2605"
    assert created.p_code == "WM-141"


def test_manual_lot_create_without_po_is_valid() -> None:
    created = PlotCycleCreate(lotNo="MANUAL-LOT-1", pCode="X", cycleLabel="jun2026")
    assert created.po_number is None
    assert created.lot_no == "MANUAL-LOT-1"
