"""Admin inspection-protocol endpoints (round 5.5) — list grouped by stage +
label-only PATCH. Repository mocked (no DB fixture).
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.inspection_protocols_admin import (
    bulk_update_inspection_protocol_criteria,
    list_admin_inspection_protocols,
    update_inspection_protocol_criterion,
)
from app.schemas.inspection_protocol import (
    InspectionProtocolBulkUpdate,
    InspectionProtocolCriterionUpdate,
)

_MODULE = "app.api.v1.inspection_protocols_admin"


def _criterion(stage: str, slot: str, label: str, order: int, **overrides):
    defaults = dict(
        id=uuid4(), growth_stage=stage, slot=slot, label=label,
        order_index=order, active=True,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        updated_at=datetime.datetime.now(datetime.timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _two_stage_rows():
    return [
        _criterion("ระยะงอก", "fieldPrepScore", "การเตรียมแปลง", 0),
        _criterion("ระยะงอก", "weatherScore", "สภาพอากาศ", 1),
        _criterion("ระยะงอก", "careScore", "การดูแลรักษา", 2),
        _criterion("ระยะงอก", "varietyResistanceScore", "ความต้านทานของสายพันธุ์", 3),
        _criterion("เจริญเติบโต", "fieldPrepScore", "สภาพอากาศ", 0),
        _criterion("เจริญเติบโต", "weatherScore", "การดูแลรักษา", 1),
        _criterion("เจริญเติบโต", "careScore", "ความเสี่ยง", 2),
        _criterion("เจริญเติบโต", "varietyResistanceScore", "สภาพแปลง", 3),
    ]


async def test_list_groups_criteria_by_stage_with_ids() -> None:
    with patch(f"{_MODULE}.repo.list_all_criteria", AsyncMock(return_value=_two_stage_rows())):
        result = await list_admin_inspection_protocols(db=object())

    assert result.version == 1
    assert [s.growth_stage for s in result.stages] == ["ระยะงอก", "เจริญเติบโต"]
    for stage in result.stages:
        assert [c.slot for c in stage.criteria] == [
            "fieldPrepScore", "weatherScore", "careScore", "varietyResistanceScore",
        ]
        assert all(c.id is not None for c in stage.criteria)


async def test_update_criterion_sets_the_new_label() -> None:
    row = _criterion("ระยะงอก", "fieldPrepScore", "เดิม", 0)

    async def _fake_update(db, criterion, label):
        criterion.label = label
        return criterion

    with patch(f"{_MODULE}.repo.get_criterion", AsyncMock(return_value=row)), \
         patch(f"{_MODULE}.repo.update_criterion_label", _fake_update):
        result = await update_inspection_protocol_criterion(
            criterion_id=row.id,
            payload=InspectionProtocolCriterionUpdate(label="  ป้ายใหม่  "),
            db=object(),
        )

    assert result.label == "ป้ายใหม่"  # trimmed


async def test_update_missing_criterion_404() -> None:
    with patch(f"{_MODULE}.repo.get_criterion", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.repo.update_criterion_label", AsyncMock()) as mocked_update:
        with pytest.raises(HTTPException) as exc:
            await update_inspection_protocol_criterion(
                criterion_id=uuid4(),
                payload=InspectionProtocolCriterionUpdate(label="x"),
                db=object(),
            )

    assert exc.value.status_code == 404
    mocked_update.assert_not_awaited()


def test_update_schema_rejects_blank_label() -> None:
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        InspectionProtocolCriterionUpdate(label="")


# --- bulk update (round 5.6) -------------------------------------------------

async def test_bulk_updates_multiple_labels_and_trims() -> None:
    rows = [
        _criterion("ระยะงอก", "fieldPrepScore", "เดิม1", 0),
        _criterion("ระยะงอก", "weatherScore", "เดิม2", 1),
    ]
    payload = InspectionProtocolBulkUpdate(items=[
        {"id": str(rows[0].id), "label": "  ใหม่1  "},
        {"id": str(rows[1].id), "label": "ใหม่2"},
    ])
    db = MagicMock()
    db.flush = AsyncMock()

    with patch(f"{_MODULE}.repo.get_criteria_by_ids", AsyncMock(return_value=rows)):
        result = await bulk_update_inspection_protocol_criteria(payload=payload, db=db)

    assert [r.label for r in result] == ["ใหม่1", "ใหม่2"]  # trimmed
    assert rows[0].label == "ใหม่1" and rows[1].label == "ใหม่2"
    db.flush.assert_awaited_once()


async def test_bulk_missing_id_fails_the_whole_batch_without_writing() -> None:
    present = _criterion("ระยะงอก", "fieldPrepScore", "เดิม", 0)
    missing_id = uuid4()
    payload = InspectionProtocolBulkUpdate(items=[
        {"id": str(present.id), "label": "ใหม่"},
        {"id": str(missing_id), "label": "ใหม่2"},
    ])
    db = MagicMock()
    db.flush = AsyncMock()

    # Repo returns only the one that exists.
    with patch(f"{_MODULE}.repo.get_criteria_by_ids", AsyncMock(return_value=[present])):
        with pytest.raises(HTTPException) as exc:
            await bulk_update_inspection_protocol_criteria(payload=payload, db=db)

    assert exc.value.status_code == 404
    # Nothing written — the present row's label is untouched and no flush ran.
    assert present.label == "เดิม"
    db.flush.assert_not_awaited()


def test_bulk_schema_rejects_a_blank_label() -> None:
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        InspectionProtocolBulkUpdate(items=[{"id": str(uuid4()), "label": "   "}])


def test_bulk_schema_rejects_an_empty_item_list() -> None:
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        InspectionProtocolBulkUpdate(items=[])
