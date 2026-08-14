"""Auto Lot generator + PO/P.Code/cycleLabel/supplierLotNo normalization (the
single central helper in app/services/lot_number.py).

Round 8-5A shipped V1 ({PO}-{plotCode}-{running}, 2-digit minimum); round
8-12A replaced it with V2:

    {cycleLabel}-{supplierCode}-{pCode}-{running}    (3-digit minimum)

Pure-function tests, no DB.
"""
from __future__ import annotations

import pytest

from app.services.lot_number import (
    MAX_LOT_NO_LENGTH,
    AutoLotMissingComponentError,
    LotNumberTooLongError,
    auto_lot_preview,
    build_auto_lot_series_key,
    format_auto_lot_no,
    normalize_cycle_label,
    normalize_p_code,
    normalize_po_number,
    normalize_supplier_lot_no,
)

_LABEL = "2605"
_SUPPLIER = "SUP010"
_PCODE = "WM-141"


def _lot(running: int, label: str = _LABEL, supplier: str = _SUPPLIER, p_code: str = _PCODE) -> str:
    return format_auto_lot_no(
        cycle_label=label, supplier_code=supplier, p_code=p_code, running=running,
    )


# --- PO normalization (unchanged by V2 — PO is still a stored field) --------

def test_po_trimmed_and_uppercased() -> None:
    assert normalize_po_number("  po25001  ") == "PO25001"


def test_po_already_upper_is_idempotent() -> None:
    assert normalize_po_number("PO25001") == "PO25001"
    # Idempotent — applying twice equals applying once (schema + repo both call it).
    assert normalize_po_number(normalize_po_number("po25001")) == "PO25001"


@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
def test_po_blank_becomes_none(blank: str | None) -> None:
    assert normalize_po_number(blank) is None


# --- P.Code normalization ---------------------------------------------------

def test_p_code_trimmed_case_preserved() -> None:
    # Unlike PO, P.Code keeps its case.
    assert normalize_p_code("  MelonA-01  ") == "MelonA-01"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_p_code_blank_becomes_none(blank: str | None) -> None:
    assert normalize_p_code(blank) is None


# --- cycleLabel normalization (round 8-12A) --------------------------------

def test_cycle_label_is_trimmed_only() -> None:
    assert normalize_cycle_label("  2605  ") == "2605"


@pytest.mark.parametrize("label", ["2605", "26-may", "MAY26", "รอบทดลอง", "26 May 2026"])
def test_cycle_label_accepts_any_shape_verbatim(label: str) -> None:
    """No YYMM regex, no date parsing, no case folding — the label is whatever
    the field team writes, and it must survive into the lot unchanged."""
    assert normalize_cycle_label(label) == label


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_cycle_label_blank_becomes_none(blank: str | None) -> None:
    assert normalize_cycle_label(blank) is None


# --- supplierLotNo normalization (round 8-12A) -----------------------------

def test_supplier_lot_no_trimmed_case_preserved() -> None:
    assert normalize_supplier_lot_no("  sup-Own-9  ") == "sup-Own-9"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_supplier_lot_no_blank_becomes_none(blank: str | None) -> None:
    assert normalize_supplier_lot_no(blank) is None


# --- Auto Lot V2 formatting -------------------------------------------------

def test_the_contract_example_from_the_round_brief() -> None:
    assert _lot(3) == "2605-SUP010-WM-141-003"


@pytest.mark.parametrize(
    "running,expected_suffix",
    [
        (1, "-001"),     # min width 3
        (9, "-009"),
        (99, "-099"),
        (100, "-100"),
        (999, "-999"),
        (1000, "-1000"),  # grows past 3 digits, never wraps back to 001
    ],
)
def test_running_zero_padded_min_width_three(running: int, expected_suffix: str) -> None:
    assert _lot(running) == f"{_LABEL}-{_SUPPLIER}-{_PCODE}{expected_suffix}"


def test_999_to_1000_does_not_truncate_or_wrap() -> None:
    assert _lot(999).endswith("-999")
    assert _lot(1000).endswith("-1000")
    assert _lot(1000) != _lot(1)


def test_format_uses_label_supplier_pcode_running_order() -> None:
    assert _lot(5, label="L", supplier="S", p_code="P") == "L-S-P-005"


def test_po_is_not_part_of_the_formula() -> None:
    """V1 led with the PO; V2 does not mention it at all."""
    lot = _lot(1)
    assert "PO" not in lot.replace(_SUPPLIER, "")


# --- components are used IN FULL -------------------------------------------

def test_p_code_is_never_clipped_to_three_characters() -> None:
    assert _lot(1, p_code="WM-141") == "2605-SUP010-WM-141-001"
    assert "WM-141" in _lot(1, p_code="WM-141")


def test_arbitrary_cycle_labels_survive_into_the_lot() -> None:
    assert _lot(4, label="26-may") == "26-may-SUP010-WM-141-004"
    assert _lot(1000, label="MAY26", p_code="ABC") == "MAY26-SUP010-ABC-1000"


def test_supplier_code_is_used_in_full() -> None:
    assert _lot(1, supplier="SUPPLIER-LONG-CODE").startswith("2605-SUPPLIER-LONG-CODE-")


def test_components_are_trimmed_before_use() -> None:
    assert _lot(1, label="  2605  ", p_code="  WM-141  ") == "2605-SUP010-WM-141-001"


def test_a_unicode_cycle_label_is_accepted() -> None:
    lot = _lot(2, label="รอบทดลอง")
    assert lot == "รอบทดลอง-SUP010-WM-141-002"


# --- required components ----------------------------------------------------

@pytest.mark.parametrize(
    "label,supplier,p_code,missing",
    [
        ("", _SUPPLIER, _PCODE, "cycleLabel"),
        ("   ", _SUPPLIER, _PCODE, "cycleLabel"),
        (_LABEL, "", _PCODE, "supplierCode"),
        (_LABEL, _SUPPLIER, "", "pCode"),
        (_LABEL, _SUPPLIER, "   ", "pCode"),
    ],
)
def test_blank_component_raises_naming_the_field(label, supplier, p_code, missing) -> None:
    with pytest.raises(AutoLotMissingComponentError) as exc:
        format_auto_lot_no(
            cycle_label=label, supplier_code=supplier, p_code=p_code, running=1,
        )
    assert missing in exc.value.missing
    assert isinstance(exc.value, ValueError)   # endpoints map to 422


def test_every_missing_component_is_reported_at_once() -> None:
    with pytest.raises(AutoLotMissingComponentError) as exc:
        format_auto_lot_no(cycle_label="", supplier_code="", p_code="", running=1)
    assert exc.value.missing == ("cycleLabel", "supplierCode", "pCode")


def test_the_error_never_echoes_a_submitted_value() -> None:
    with pytest.raises(AutoLotMissingComponentError) as exc:
        format_auto_lot_no(
            cycle_label="  ", supplier_code=_SUPPLIER, p_code="SECRET-PCODE", running=1,
        )
    assert "SECRET-PCODE" not in str(exc.value)


def test_running_must_be_positive() -> None:
    with pytest.raises(ValueError):
        _lot(0)


# --- Auto Lot length guard --------------------------------------------------

def test_generated_lot_within_limit_is_returned() -> None:
    assert len(_lot(1)) <= MAX_LOT_NO_LENGTH


def test_generated_lot_over_limit_raises_clean_error_not_truncated() -> None:
    with pytest.raises(LotNumberTooLongError) as exc:
        format_auto_lot_no(
            cycle_label="L" * 50, supplier_code="S" * 30, p_code="P" * 30, running=1,
        )
    # A clear message, never a silently truncated value.
    assert str(MAX_LOT_NO_LENGTH) in str(exc.value)
    assert isinstance(exc.value, ValueError)  # endpoints map to 422


def test_overlength_error_names_the_v2_components_not_po_or_plot_code() -> None:
    """The V1 message told users to shorten the PO or plot code — neither is
    part of V2, so that advice would send them to the wrong field."""
    with pytest.raises(LotNumberTooLongError) as exc:
        format_auto_lot_no(
            cycle_label="L" * 50, supplier_code="S" * 30, p_code="P" * 30, running=1,
        )
    msg = str(exc.value)
    assert "cycleLabel" in msg and "pCode" in msg
    assert "plot code" not in msg.lower()
    assert "PO number" not in msg


def test_boundary_running_width_growth_can_overflow_by_one() -> None:
    """A lot that fits exactly at 3 digits overflows when running reaches 4 —
    which is why the import pre-check probes at running=1000, not 1."""
    # label(40) + "-" + supplier(30) + "-" + pcode(24) + "-" + "999" = 100
    label, supplier, p_code = "L" * 40, "S" * 30, "P" * 24
    fit = format_auto_lot_no(
        cycle_label=label, supplier_code=supplier, p_code=p_code, running=999,
    )
    assert len(fit) == MAX_LOT_NO_LENGTH
    with pytest.raises(LotNumberTooLongError):
        format_auto_lot_no(
            cycle_label=label, supplier_code=supplier, p_code=p_code, running=1000,
        )


# --- series key -------------------------------------------------------------

def test_series_key_covers_supplier_label_and_p_code() -> None:
    key = build_auto_lot_series_key(_SUPPLIER, _LABEL, _PCODE)
    for part in (_SUPPLIER, _LABEL, _PCODE):
        assert part in key


def test_series_key_distinguishes_each_component() -> None:
    base = build_auto_lot_series_key(_SUPPLIER, _LABEL, _PCODE)
    assert base != build_auto_lot_series_key("SUP011", _LABEL, _PCODE)
    assert base != build_auto_lot_series_key(_SUPPLIER, "26-may", _PCODE)
    assert base != build_auto_lot_series_key(_SUPPLIER, _LABEL, "WM-142")


def test_series_key_is_not_ambiguous_across_dashed_components() -> None:
    """A dash inside a component must not let two different series collapse
    into one key — they would then share a running sequence and could mint
    duplicate lot numbers."""
    assert (
        build_auto_lot_series_key("S", "26", "may-1")
        != build_auto_lot_series_key("S", "26-may", "1")
    )


# --- preview string ---------------------------------------------------------

def test_preview_shows_the_v2_shape_with_a_running_placeholder() -> None:
    assert auto_lot_preview(_LABEL, _SUPPLIER, _PCODE) == "2605-SUP010-WM-141-###"


def test_preview_never_shows_the_v1_shape() -> None:
    preview = auto_lot_preview(_LABEL, _SUPPLIER, _PCODE)
    assert "XX" not in preview
    assert preview.endswith("-###")


def test_preview_marks_missing_components_instead_of_raising() -> None:
    """Preview is display-only and must never blow up a read-only Preview."""
    assert auto_lot_preview(None, _SUPPLIER, _PCODE) == "<cycleLabel>-SUP010-WM-141-###"
    assert auto_lot_preview(_LABEL, None, _PCODE) == "2605-<supplierCode>-WM-141-###"
    assert auto_lot_preview(_LABEL, _SUPPLIER, None) == "2605-SUP010-<pCode>-###"
