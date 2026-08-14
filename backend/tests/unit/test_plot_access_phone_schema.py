"""Access-phone schemas (round 8-3A).

PlotAccessPhoneConfig normalizes every number at the API boundary and REJECTS
(never silently dedupes) duplicates / a primary repeated in additional; the
server-derived Record phone fields must not be forgeable through any create/
update schema.

Round 8-17C — normalization/validation moved from a Pydantic model_validator
to normalize_and_validate_phone_config(), a plain function every endpoint
calls by hand (see app/schemas/plot.py's docstrings for why: a
model_validator's ValueError round-trips through FastAPI's automatic
RequestValidationError handler, which echoes the rejected raw phone back in
the response — a PII leak). So PlotAccessPhoneConfig(...) construction no
longer normalizes or rejects anything by itself; every test below calls
normalize_and_validate_phone_config() explicitly, same as the real endpoints
do. Business rules/messages are UNCHANGED from the original validator.
"""
from __future__ import annotations

import datetime
from uuid import uuid4

import pytest

from app.schemas.plot import (
    PlotAccessPhoneConfig,
    PlotRead,
    PlotSummary,
    normalize_and_validate_phone_config,
)
from app.schemas.record import (
    PublicRecordCreate,
    RecordCreate,
    RecordRead,
    RecordUpdate,
)

_SERVER_DERIVED = {
    "plot_access_phone_id",
    "submitted_phone_snapshot",
    "submitted_phone_type",
    "inspector_type",
}


# --- config: valid shapes ---------------------------------------------------

def test_one_primary_plus_many_additional_valid_and_normalized() -> None:
    c = PlotAccessPhoneConfig(
        primaryPhone="084-555-2162",
        additionalPhones=["+66812345678", "091 234 5678"],
    )
    normalize_and_validate_phone_config(c)
    assert c.primary_phone == "0845552162"
    assert c.additional_phones == ["0812345678", "0912345678"]


def test_primary_only_no_additional_valid() -> None:
    c = PlotAccessPhoneConfig(primaryPhone="0845552162")
    normalize_and_validate_phone_config(c)
    assert c.primary_phone == "0845552162"
    assert c.additional_phones == []


def test_blank_primary_becomes_null() -> None:
    c = PlotAccessPhoneConfig(primaryPhone="   ", additionalPhones=[])
    normalize_and_validate_phone_config(c)
    assert c.primary_phone is None


def test_empty_config_deactivates_all_semantics() -> None:
    c = PlotAccessPhoneConfig()
    normalize_and_validate_phone_config(c)
    assert c.primary_phone is None
    assert c.additional_phones == []


# --- config: rejections -----------------------------------------------------
# Round 8-17C — PlotAccessPhoneConfig(...) construction itself no longer
# raises (see module docstring); every rejection now happens inside
# normalize_and_validate_phone_config(), as a plain ValueError.

def test_duplicate_additional_rejected_not_deduped() -> None:
    # same number, two spellings — must be rejected, not silently collapsed
    c = PlotAccessPhoneConfig(additionalPhones=["0812345678", "081-234-5678"])
    with pytest.raises(ValueError, match="duplicate"):
        normalize_and_validate_phone_config(c)


def test_primary_duplicated_in_additional_rejected() -> None:
    c = PlotAccessPhoneConfig(primaryPhone="0812345678", additionalPhones=["081-234-5678"])
    with pytest.raises(ValueError, match="must not also appear"):
        normalize_and_validate_phone_config(c)


def test_blank_additional_entry_rejected() -> None:
    c = PlotAccessPhoneConfig(additionalPhones=[""])
    with pytest.raises(ValueError, match="blank entries"):
        normalize_and_validate_phone_config(c)


def test_additional_without_primary_rejected() -> None:
    """Round 8-3C business rule: a plot may be entirely empty, but never
    "additional-only" — any additional number requires a designated primary."""
    c = PlotAccessPhoneConfig(additionalPhones=["0812345678"])
    with pytest.raises(ValueError, match="primaryPhone is required"):
        normalize_and_validate_phone_config(c)


def test_additional_without_primary_error_message_never_concatenates_phone() -> None:
    """The message TEXT this check raises never embeds the raw phone — a
    fixed string, not an f-string interpolating the value.

    Round 8-17C fix: since normalize_and_validate_phone_config() is a plain
    function (not a Pydantic model_validator anymore), a caller that catches
    its ValueError and hand-builds HTTPException(422, detail=str(exc)) —
    which every endpoint that uses this schema now does — produces a 422
    response with NO Pydantic-generated `input` key at all: the response
    body is just {"detail": "<this message>"}, nothing else. The PII-echo
    hole this test used to only partially document (message text was always
    safe; the surrounding Pydantic error envelope was the actual leak) is
    now fully closed, not just noted."""
    c = PlotAccessPhoneConfig(additionalPhones=["0812345678"])
    try:
        normalize_and_validate_phone_config(c)
    except ValueError as exc:
        assert "0812345678" not in str(exc)
    else:  # pragma: no cover - the input above always raises
        raise AssertionError("expected ValueError")


def test_blank_primary_with_additional_rejected_not_silently_promoted() -> None:
    """A blank primaryPhone means null (see test_blank_primary_becomes_null) —
    it must NOT be silently treated as "no primary needed" when additional
    numbers are present."""
    c = PlotAccessPhoneConfig(primaryPhone="   ", additionalPhones=["0812345678"])
    with pytest.raises(ValueError, match="primaryPhone is required"):
        normalize_and_validate_phone_config(c)


def test_invalid_number_rejected() -> None:
    c = PlotAccessPhoneConfig(primaryPhone="0712345678")
    with pytest.raises(ValueError):
        normalize_and_validate_phone_config(c)


def test_max_additional_enforced() -> None:
    """Round 8-17C.1: additionalPhones is SkipValidation now (see module
    docstring), so max_length=10 is no longer a Field constraint — the count
    is checked by hand inside normalize_and_validate_phone_config(), same as
    every other rule, so an over-length list is never echoed at the
    Pydantic-auto-422 layer."""
    eleven = [f"08100000{i:02d}" for i in range(11)]
    c = PlotAccessPhoneConfig(additionalPhones=eleven)
    with pytest.raises(ValueError, match="more than 10"):
        normalize_and_validate_phone_config(c)


# --- round 8-17C.1: wrong-typed payloads never raise at construction (both
# fields are SkipValidation) and are rejected by hand, generically ---------

def test_schema_primary_phone_wrong_type_does_not_raise_at_construction() -> None:
    c = PlotAccessPhoneConfig(primaryPhone=812345678)
    assert c.primary_phone == 812345678


def test_schema_additional_phones_wrong_type_does_not_raise_at_construction() -> None:
    c = PlotAccessPhoneConfig(additionalPhones="0812345678")
    assert c.additional_phones == "0812345678"


@pytest.mark.parametrize("bad_primary", [812345678, 845552162.0, ["0812345678"], {"n": "0812345678"}, True])
def test_primary_phone_wrong_type_rejected_by_normalize(bad_primary) -> None:
    c = PlotAccessPhoneConfig(primaryPhone=bad_primary)
    with pytest.raises(ValueError, match="string or null"):
        normalize_and_validate_phone_config(c)


def test_additional_phones_not_a_list_rejected_by_normalize() -> None:
    c = PlotAccessPhoneConfig(additionalPhones="0812345678")
    with pytest.raises(ValueError, match="must be a list"):
        normalize_and_validate_phone_config(c)


@pytest.mark.parametrize("bad_item", [812345678, 845552162.0, ["0812345678"], {"n": "0812345678"}, None, True])
def test_additional_phones_item_wrong_type_rejected_by_normalize(bad_item) -> None:
    c = PlotAccessPhoneConfig(additionalPhones=[bad_item])
    with pytest.raises(ValueError, match="must be strings"):
        normalize_and_validate_phone_config(c)


def test_ten_additional_allowed() -> None:
    ten = [f"08100000{i:02d}" for i in range(10)]
    c = PlotAccessPhoneConfig(primaryPhone="0845552162", additionalPhones=ten)
    normalize_and_validate_phone_config(c)
    assert len(c.additional_phones) == 10


# --- Record schemas: server-derived phone fields are not client-forgeable ---

@pytest.mark.parametrize("model", [RecordCreate, PublicRecordCreate, RecordUpdate])
def test_record_write_schemas_have_no_server_derived_phone_fields(model) -> None:
    assert _SERVER_DERIVED.isdisjoint(set(model.model_fields)), model.__name__


def test_record_read_exposes_nullable_phone_fields() -> None:
    assert _SERVER_DERIVED <= set(RecordRead.model_fields)
    # nullable + default None: a record with none of them set still validates.
    read = RecordRead.model_validate(_bare_record())
    assert read.plot_access_phone_id is None
    assert read.submitted_phone_snapshot is None
    assert read.submitted_phone_type is None
    assert read.inspector_type is None


def test_record_read_carries_phone_values_when_present() -> None:
    phone_id = uuid4()
    read = RecordRead.model_validate(
        _bare_record(
            plot_access_phone_id=phone_id,
            submitted_phone_snapshot="0845552162",
            submitted_phone_type="primary",
            inspector_type="farmer",
        )
    )
    assert read.plot_access_phone_id == phone_id
    assert read.submitted_phone_snapshot == "0845552162"
    assert read.submitted_phone_type == "primary"
    assert read.inspector_type == "farmer"


# --- PlotRead / PlotSummary read-only phone fields default cleanly ----------

def test_plot_read_and_summary_phone_fields_default_empty() -> None:
    assert PlotRead.model_fields["primary_phone"].default is None
    assert PlotSummary.model_fields["primary_phone"].default is None


def _bare_record(**overrides):
    from types import SimpleNamespace

    data = dict(
        id=uuid4(), plot_id=uuid4(), plot_cycle_id=uuid4(), cycle_no=1,
        cycle_status="active", cycle_label=None, cycle_crop=None, cycle_variety=None,
        cycle_lot_no=None, cycle_planting_date=None, cycle_plant_count=None,
        cycle_expected_yield_full=None, cycle_expected_yield_unit=None,
        supplier_id=uuid4(), recorded_by_id=uuid4(),
        submitted_by_code="FIELD007", submitted_by_name=None, submitted_ip=None,
        record_date=datetime.date(2026, 7, 1),
        crop=None, variety=None, growth_stage=None, planting_date=None, yield_pct=None,
        weather_condition=None, field_prep_score=None, weather_score=None,
        care_score=None, variety_resistance_score=None, recommendation=None, notes=None,
        latitude=None, longitude=None, photo_urls=[], custom_fields={}, is_active=True,
        plot_access_phone_id=None, submitted_phone_snapshot=None,
        submitted_phone_type=None, inspector_type=None,
        created_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
        updated_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
    )
    data.update(overrides)
    return SimpleNamespace(**data)
