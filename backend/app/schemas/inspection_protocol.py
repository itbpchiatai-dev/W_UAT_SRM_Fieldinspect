"""Inspection protocol read schemas — round 5.1.

Response shape for GET /api/v1/inspection-protocols and its public sibling.
Exposes only the protocol version and, per growth stage, the ordered
slot/label pairs the record form should render for its 4 score inputs. No
scores, secrets, tokens, or hashes — this is static reference data whose
source of truth is app/services/inspection_protocols.py.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelBaseModel


class InspectionProtocolCriterion(CamelBaseModel):
    # `slot` is a camelCase score-field name (e.g. "fieldPrepScore") — it's
    # data, not a schema field name, so the alias generator leaves it as-is.
    slot: str
    label: str


class InspectionProtocolStage(CamelBaseModel):
    growth_stage: str
    criteria: list[InspectionProtocolCriterion]


class InspectionProtocolList(CamelBaseModel):
    version: int
    stages: list[InspectionProtocolStage]


# --- Admin editor (round 5.5) — carries the row id + order/active so a label
# can be edited; the public/form read schemas above stay minimal (slot/label).


class InspectionProtocolAdminCriterion(CamelBaseModel):
    id: UUID
    growth_stage: str
    slot: str
    label: str
    order_index: int
    active: bool


class InspectionProtocolAdminStage(CamelBaseModel):
    growth_stage: str
    criteria: list[InspectionProtocolAdminCriterion]


class InspectionProtocolAdminList(CamelBaseModel):
    version: int
    stages: list[InspectionProtocolAdminStage]


class InspectionProtocolCriterionUpdate(CamelBaseModel):
    """Round 5.5 edits labels only — slot binding and the 4-per-stage shape
    are immutable."""

    label: str = Field(..., min_length=1, max_length=255)


class InspectionProtocolCriterionBulkItem(CamelBaseModel):
    id: UUID
    label: str = Field(..., min_length=1, max_length=255)

    @field_validator("label")
    @classmethod
    def _label_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("label cannot be blank")
        return v


class InspectionProtocolBulkUpdate(CamelBaseModel):
    """Round 5.6 — atomic multi-label edit (one transaction) so a stage's 4
    labels never partially save."""

    items: list[InspectionProtocolCriterionBulkItem] = Field(..., min_length=1)
