"""Round C — ผลผลิตหลังทำความสะอาด captured on a Record (migration 0054).

Covers the column/constraint, the shared input boundary, and the read-model
exposure. What the field is FOR (a "ผลผลิตสุดท้าย" inspection) is a frontend
concern; what matters here is that the value survives the create path
unaltered, cannot be negative or over-precise, and never touches yield_pct.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db.models.record import Record
from app.schemas.record import PublicRecordCreate, RecordCreate, RecordRead

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_09_04_0100-0054_records_final_yield_after_clean.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


# --- migration --------------------------------------------------------------

def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0054_records_final_yield_clean"
    assert down == "0053_plots_auto_plot_code"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_adds_one_nullable_column_with_a_non_negative_check() -> None:
    up = _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]
    assert "ALTER TABLE records ADD COLUMN final_yield_after_clean NUMERIC(14, 2)" in up
    assert "NOT NULL" not in up.replace("IS NULL OR final_yield_after_clean >= 0", "")
    assert "ck_records_final_yield_after_clean_non_negative" in up
    # additive and data-free: every existing record reads NULL
    assert "UPDATE " not in up
    assert "INSERT INTO" not in up
    assert "DELETE " not in up
    assert set(re.findall(r"ALTER TABLE (\w+)", up)) == {"records"}


def test_width_matches_the_cycle_column_it_is_copied_into() -> None:
    """plot_cycles.final_yield_after_clean is NUMERIC(14,2) (migration 0043).
    A narrower record column would make a value that round D cannot copy."""
    up = _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]
    assert "NUMERIC(14, 2)" in up


def test_model_metadata_matches_the_migration() -> None:
    assert "final_yield_after_clean" in {c.name for c in Record.__table__.columns}
    names = {c.name for c in Record.__table__.constraints if c.name}
    assert "ck_records_final_yield_after_clean_non_negative" in names


def test_no_harvest_date_or_unit_column_was_added() -> None:
    """Round C's design point: record_date already says when, and the figure is
    always kilograms (server-stamped). Adding either would be a second source
    of truth for something this system already decides."""
    columns = {c.name for c in Record.__table__.columns}
    assert "harvest_date" not in columns
    assert "final_yield_unit" not in columns
    # and "ผลผลิตที่เก็บได้" reuses the kg the record already carries
    assert "harvest_yield" not in columns
    assert "yield_quantity_kg" in columns


# --- input boundary ---------------------------------------------------------

def _create_kwargs(**over):
    from uuid import uuid4
    import datetime

    base = dict(
        plotId=uuid4(), supplierId=uuid4(), recordDate=datetime.date(2026, 5, 20),
    )
    base.update(over)
    return base


def test_optional_everywhere_so_an_older_client_is_unaffected() -> None:
    payload = RecordCreate(**_create_kwargs())
    assert payload.final_yield_after_clean is None


def test_accepts_a_two_decimal_value_on_both_create_schemas() -> None:
    logged_in = RecordCreate(**_create_kwargs(finalYieldAfterClean="1180.50"))
    assert logged_in.final_yield_after_clean == Decimal("1180.50")

    public = PublicRecordCreate(
        inspectionSessionToken="t", recordDate="2026-05-20",
        finalYieldAfterClean="1180.50",
    )
    assert public.final_yield_after_clean == Decimal("1180.50")


@pytest.mark.parametrize("bad", ["-1", "-0.01"])
def test_a_negative_value_is_rejected(bad) -> None:
    with pytest.raises(ValidationError):
        RecordCreate(**_create_kwargs(finalYieldAfterClean=bad))


def test_three_decimal_places_are_rejected_never_silently_rounded() -> None:
    """Same "reject, don't round" contract as yield_quantity_kg: a typo must be
    a 422 the user sees, not a number quietly changed under them."""
    with pytest.raises(ValidationError):
        RecordCreate(**_create_kwargs(finalYieldAfterClean="1180.505"))


def test_a_value_too_large_for_the_column_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RecordCreate(**_create_kwargs(finalYieldAfterClean="1" * 13 + ".00"))


def test_both_create_schemas_share_one_boundary() -> None:
    """One rule, never duplicated: the two flows must not drift on what counts
    as a valid figure."""
    a = RecordCreate.model_fields["final_yield_after_clean"].metadata
    b = PublicRecordCreate.model_fields["final_yield_after_clean"].metadata
    assert a == b


# --- it never becomes a percentage ------------------------------------------

def test_it_is_not_an_input_to_yield_derivation() -> None:
    """The percentage a record reports is always measured against
    ผลผลิตที่เก็บได้ (yield_quantity_kg), so a cycle's percentages stay
    comparable across its whole life. Source-level guard on the one module that
    derives yield_pct."""
    import inspect

    from app.services import yield_calculation

    assert "final_yield_after_clean" not in inspect.getsource(yield_calculation)


# --- read model -------------------------------------------------------------

def test_read_model_exposes_it_camel_cased_and_defaults_to_null() -> None:
    import datetime
    from uuid import uuid4

    read = RecordRead(
        id=uuid4(), plotId=uuid4(), supplierId=uuid4(),
        recordDate=datetime.date(2026, 5, 20),
        submittedByName=None, crop=None, variety=None, growthStage=None,
        plantingDate=None, yieldPct=None,
        weatherCondition=None, fieldPrepScore=None, weatherScore=None,
        careScore=None, varietyResistanceScore=None,
        recommendation=None, notes=None, latitude=None, longitude=None,
        photoUrls=[], customFields={}, isActive=True,
        recordedById=uuid4(), submittedByCode=None,
        createdAt=datetime.datetime.now(datetime.timezone.utc),
        updatedAt=datetime.datetime.now(datetime.timezone.utc),
    )
    dumped = read.model_dump(by_alias=True)
    assert "finalYieldAfterClean" in dumped
    assert dumped["finalYieldAfterClean"] is None


def test_the_server_never_derives_it_from_anything() -> None:
    """Unlike yield_target_kg_snapshot (server-derived, absent from every
    create schema), this is a plain user-entered figure: it must be a real
    create field on both flows, or the field team's number would be dropped."""
    assert "final_yield_after_clean" in RecordCreate.model_fields
    assert "final_yield_after_clean" in PublicRecordCreate.model_fields
    assert "yield_target_kg_snapshot" not in RecordCreate.model_fields
