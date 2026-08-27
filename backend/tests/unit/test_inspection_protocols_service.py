"""inspection_protocols service — map-based registry + snapshot builder
(round 5.5: DB-backed with a built-in fallback).

Pure functions plus get_protocol_map (DB config or fallback), with the
repository mocked — no DB fixture in this repo.
"""
from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.schemas.record import RecordCreate
from app.services import inspection_protocols as svc

_STAGES = ["ระยะงอก", "เจริญเติบโต", "ออกดอก", "ติดผล", "เก็บเกี่ยว"]
_SLOT_NAMES = ["fieldPrepScore", "weatherScore", "careScore", "varietyResistanceScore"]
_REPO = "app.repositories.inspection_protocol_repository"


def _record(**overrides) -> RecordCreate:
    defaults = dict(
        plot_id=uuid4(), supplier_id=uuid4(),
        record_date=datetime.date(2026, 7, 1), submitted_by_code="FIELD01",
    )
    defaults.update(overrides)
    return RecordCreate(**defaults)


def _protocol_record(stage: str = "ระยะงอก", **overrides) -> RecordCreate:
    base = dict(
        growth_stage=stage,
        field_prep_score=8, weather_score=7, care_score=9, variety_resistance_score=6,
    )
    base.update(overrides)
    return _record(**base)


def _map(**stages) -> dict:
    """Build a protocol map from {stage: [labels x4]} in slot order."""
    return {
        stage: [{"slot": slot, "label": label} for slot, label in zip(_SLOT_NAMES, labels)]
        for stage, labels in stages.items()
    }


# --- default map / registry shape --------------------------------------------

def test_protocol_version_is_1() -> None:
    assert svc.PROTOCOL_VERSION == 1


def test_default_map_has_five_stages_each_with_four_slots() -> None:
    m = svc.default_protocol_map()
    assert list(m.keys()) == _STAGES
    for stage, criteria in m.items():
        assert [c["slot"] for c in criteria] == _SLOT_NAMES, stage
        assert all(c["label"].strip() for c in criteria)


def test_list_protocols_from_map_shape() -> None:
    out = svc.list_protocols_from_map(_map(**{"ระยะงอก": ["a", "b", "c", "d"]}))
    assert out == [{
        "growth_stage": "ระยะงอก",
        "criteria": [
            {"slot": "fieldPrepScore", "label": "a"},
            {"slot": "weatherScore", "label": "b"},
            {"slot": "careScore", "label": "c"},
            {"slot": "varietyResistanceScore", "label": "d"},
        ],
    }]


# --- get_protocol_for_stage --------------------------------------------------

def test_get_protocol_for_stage_known_none_and_unknown() -> None:
    m = svc.default_protocol_map()
    assert svc.get_protocol_for_stage(m, "ออกดอก") is not None
    assert svc.get_protocol_for_stage(m, None) is None
    assert svc.get_protocol_for_stage(m, "ตั้งตัว") is None  # supplement stage


# --- build_snapshot ----------------------------------------------------------

def test_build_snapshot_uses_the_maps_labels_and_the_scores() -> None:
    m = _map(**{"เจริญเติบโต": ["สภาพอากาศ", "การดูแลรักษา", "ความเสี่ยง", "สภาพแปลง"]})
    snap = svc.build_snapshot("เจริญเติบโต", {
        "field_prep_score": 8, "weather_score": 7, "care_score": 9, "variety_resistance_score": 6,
    }, m)
    assert snap["version"] == 1
    assert snap["growthStage"] == "เจริญเติบโต"
    assert snap["criteria"] == [
        {"slot": "fieldPrepScore", "label": "สภาพอากาศ", "score": 8},
        {"slot": "weatherScore", "label": "การดูแลรักษา", "score": 7},
        {"slot": "careScore", "label": "ความเสี่ยง", "score": 9},
        {"slot": "varietyResistanceScore", "label": "สภาพแปลง", "score": 6},
    ]


# Round 8-27B — the scores became optional. Through 8-27A a missing score
# raised ProtocolValidationError (-> 422), which left an inspector who could
# only judge some of the criteria unable to submit at all. A blank is now
# frozen as null, so the snapshot still records WHICH criteria applied and
# WHICH were left unscored.
def test_build_snapshot_keeps_a_missing_score_as_null() -> None:
    m = svc.default_protocol_map()
    snap = svc.build_snapshot("ระยะงอก", {
        "field_prep_score": 8, "weather_score": None,
        "care_score": 9, "variety_resistance_score": 6,
    }, m)
    by_slot = {c["slot"]: c for c in snap["criteria"]}
    assert by_slot["weatherScore"]["score"] is None
    # The unscored criterion keeps its label — the point of the snapshot.
    assert by_slot["weatherScore"]["label"] == "ความสมบูรณ์ของต้นกล้า"
    assert by_slot["fieldPrepScore"]["score"] == 8


def test_build_snapshot_accepts_every_score_missing() -> None:
    snap = svc.build_snapshot("ระยะงอก", {}, svc.default_protocol_map())
    assert len(snap["criteria"]) == 4
    assert all(c["score"] is None for c in snap["criteria"])
    assert [c["label"] for c in snap["criteria"]] == list(svc.DEFAULT_PROTOCOLS["ระยะงอก"])


def test_build_snapshot_rejects_a_stage_not_in_the_map() -> None:
    with pytest.raises(svc.ProtocolValidationError):
        svc.build_snapshot("ตั้งตัว", {
            "field_prep_score": 8, "weather_score": 7,
            "care_score": 9, "variety_resistance_score": 6,
        }, svc.default_protocol_map())


# --- apply_protocol_snapshot -------------------------------------------------

def test_apply_injects_server_snapshot_for_a_protocol_stage() -> None:
    m = svc.default_protocol_map()
    result = svc.apply_protocol_snapshot(_protocol_record("ระยะงอก"), m)
    snap = result.custom_fields[svc.SNAPSHOT_KEY]
    assert snap["growthStage"] == "ระยะงอก"
    assert [c["score"] for c in snap["criteria"]] == [8, 7, 9, 6]


def test_apply_uses_the_edited_label_from_the_map() -> None:
    # An admin renamed fieldPrepScore's label for ระยะงอก — the snapshot must
    # freeze the NEW label, proving the create path reads the config, not a
    # hardcoded set.
    m = _map(**{"ระยะงอก": ["ป้ายที่แก้ใหม่", "สภาพอากาศ", "การดูแลรักษา", "ความต้านทานของสายพันธุ์"]})
    result = svc.apply_protocol_snapshot(_protocol_record("ระยะงอก"), m)
    assert result.custom_fields[svc.SNAPSHOT_KEY]["criteria"][0]["label"] == "ป้ายที่แก้ใหม่"


def test_apply_adds_no_snapshot_for_none_or_non_protocol_stage() -> None:
    m = svc.default_protocol_map()
    assert svc.SNAPSHOT_KEY not in svc.apply_protocol_snapshot(_record(), m).custom_fields
    assert svc.SNAPSHOT_KEY not in svc.apply_protocol_snapshot(
        _record(growth_stage="ตั้งตัว"), m
    ).custom_fields


def test_apply_accepts_a_protocol_stage_missing_a_score() -> None:
    result = svc.apply_protocol_snapshot(
        _protocol_record("ระยะงอก", care_score=None), svc.default_protocol_map(),
    )
    snap = result.custom_fields[svc.SNAPSHOT_KEY]
    by_slot = {c["slot"]: c["score"] for c in snap["criteria"]}
    assert by_slot["careScore"] is None


def test_apply_overwrites_a_client_supplied_snapshot() -> None:
    forged = {"version": 99, "growthStage": "เก็บเกี่ยว", "criteria": [{"slot": "x", "label": "hack", "score": 1}]}
    result = svc.apply_protocol_snapshot(
        _protocol_record("ระยะงอก", custom_fields={svc.SNAPSHOT_KEY: forged}),
        svc.default_protocol_map(),
    )
    snap = result.custom_fields[svc.SNAPSHOT_KEY]
    assert snap["version"] == 1 and snap["growthStage"] == "ระยะงอก"


# --- get_protocol_map: DB config vs fallback + completeness ------------------

async def test_get_protocol_map_uses_db_config_when_seeded() -> None:
    db_map = {"ระยะงอก": [
        {"slot": "fieldPrepScore", "label": "L1"},
        {"slot": "weatherScore", "label": "L2"},
        {"slot": "careScore", "label": "L3"},
        {"slot": "varietyResistanceScore", "label": "L4"},
    ]}
    with patch(f"{_REPO}.load_active_protocol_map", AsyncMock(return_value=db_map)):
        result = await svc.get_protocol_map(db=object())
    assert result == db_map


async def test_get_protocol_map_falls_back_to_default_when_table_empty() -> None:
    with patch(f"{_REPO}.load_active_protocol_map", AsyncMock(return_value={})):
        result = await svc.get_protocol_map(db=object())
    assert list(result.keys()) == _STAGES  # the built-in registry


async def test_get_protocol_map_ignores_a_stage_without_all_four_slots() -> None:
    """A misconfigured stage (missing a slot) must not half-apply — it's
    dropped, and since dropping leaves the config incomplete here, the whole
    thing falls back to the built-in default rather than a partial map."""
    partial = {"ระยะงอก": [
        {"slot": "fieldPrepScore", "label": "L1"},
        {"slot": "weatherScore", "label": "L2"},
        {"slot": "careScore", "label": "L3"},
        # varietyResistanceScore missing
    ]}
    with patch(f"{_REPO}.load_active_protocol_map", AsyncMock(return_value=partial)):
        result = await svc.get_protocol_map(db=object())
    assert list(result.keys()) == _STAGES
