"""Inspection protocol contract — round 5.1, made configurable round 5.5.

Backend is the source of truth for what the 4 field-inspection scores MEAN
at each growth stage. The 4 score COLUMNS on `records`
(field_prep_score / weather_score / care_score / variety_resistance_score)
are fixed; a protocol assigns each of those 4 fixed slots a stage-specific
human LABEL. When a record is created for a protocol stage, a snapshot of
{version, growthStage, [{slot,label,score}]} is frozen into
records.custom_fields so a historical record keeps the exact labels it was
scored under even if the protocol is later edited.

Round 5.5: the protocol is now stored in the inspection_protocol_criteria
table and editable by admins. The functions here take a *protocol map*
(stage -> ordered [{slot,label}]) loaded from the DB by get_protocol_map;
when the table is empty/unseeded, get_protocol_map returns DEFAULT_PROTOCOLS
so nothing breaks. Only stages that carry all 4 slots count as a protocol —
a partial/misconfigured stage is treated as "no protocol" (gated
pass-through), never a crash.

Gated contract (round 5.1 decision, unchanged): a record whose growth_stage
is None or a non-protocol value is created unchanged with NO snapshot and no
score requirement. Only a protocol stage triggers "all 4 scores required".
The snapshot is built solely from server-side data — a client-supplied
snapshot is always stripped and never trusted.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.schemas.record import RecordCreate

# Bump when the slot vocabulary or snapshot shape changes (not when a label
# is edited — an edit is captured per-record by the frozen snapshot instead).
PROTOCOL_VERSION = 1

# The key under records.custom_fields where the frozen snapshot lives.
SNAPSHOT_KEY = "inspectionProtocolSnapshot"

# The 4 fixed score slots, in canonical order.
# Left  = camelCase slot name stored in the snapshot / returned by the read
#         endpoints (matches RecordRead's fieldPrepScore etc.).
# Right = snake_case RecordCreate attribute the score value is read from.
PROTOCOL_SLOTS: tuple[tuple[str, str], ...] = (
    ("fieldPrepScore", "field_prep_score"),
    ("weatherScore", "weather_score"),
    ("careScore", "care_score"),
    ("varietyResistanceScore", "variety_resistance_score"),
)
SLOT_NAMES: frozenset[str] = frozenset(slot for slot, _attr in PROTOCOL_SLOTS)
_SLOT_TO_ATTR: dict[str, str] = dict(PROTOCOL_SLOTS)

# Built-in fallback registry — the round-5.1 defaults, used verbatim when the
# inspection_protocol_criteria table is empty (fresh/unseeded DB). Keys MUST
# match the seeded growth_stage master data (app/db/seed.py); the supplement
# stages there deliberately have no protocol (gated pass-through).
DEFAULT_PROTOCOLS: dict[str, tuple[str, str, str, str]] = {
    "ระยะงอก": ("การเตรียมแปลง", "สภาพอากาศ", "การดูแลรักษา", "ความต้านทานของสายพันธุ์"),
    "เจริญเติบโต": ("สภาพอากาศ", "การดูแลรักษา", "ความเสี่ยง", "สภาพแปลง"),
    "ออกดอก": ("ความสมบูรณ์ของดอก", "สภาพอากาศ", "การดูแลรักษา", "ความเสี่ยงโรคและแมลง"),
    "ติดผล": ("การติดผล", "ความสมบูรณ์ของผล", "การดูแลรักษา", "ความเสี่ยงโรคและแมลง"),
    "เก็บเกี่ยว": ("ความพร้อมเก็บเกี่ยว", "คุณภาพผลผลิต", "ปริมาณผลผลิตคาดการณ์", "สภาพแปลงก่อนเก็บเกี่ยว"),
}

# Fail fast at import if the defaults and the fixed slots ever drift apart.
assert len(PROTOCOL_SLOTS) == 4, "there must be exactly 4 score slots"
for _stage, _criteria in DEFAULT_PROTOCOLS.items():
    assert len(_criteria) == 4, f"default protocol {_stage!r} must define exactly 4 criteria"

# A protocol map is: growth_stage -> ordered list of {"slot": .., "label": ..}.
ProtocolMap = dict[str, list[dict]]


class ProtocolValidationError(ValueError):
    """A protocol stage was given but its score contract wasn't met (a
    required score is missing). The record-create endpoints turn this into
    a 422."""


def default_protocol_map() -> ProtocolMap:
    """The built-in registry as a protocol map — the fallback shape."""
    return {
        stage: [
            {"slot": slot, "label": label}
            for (slot, _attr), label in zip(PROTOCOL_SLOTS, labels)
        ]
        for stage, labels in DEFAULT_PROTOCOLS.items()
    }


def _is_complete(criteria: list[dict]) -> bool:
    """A stage counts as a protocol only when it carries all 4 slots exactly
    once — a partial/misconfigured stage is ignored (gated pass-through),
    never allowed to half-apply a contract."""
    slots = {c["slot"] for c in criteria}
    return len(criteria) == 4 and slots == SLOT_NAMES


async def get_protocol_map(db: AsyncSession) -> ProtocolMap:
    """The active protocol map: the DB config when it's seeded and complete,
    otherwise the built-in DEFAULT_PROTOCOLS so an empty/unseeded table never
    breaks the forms or record creation."""
    from app.repositories import inspection_protocol_repository as repo

    grouped = await repo.load_active_protocol_map(db)
    complete = {stage: crits for stage, crits in grouped.items() if _is_complete(crits)}
    return complete or default_protocol_map()


def list_protocols_from_map(protocol_map: ProtocolMap) -> list[dict]:
    """The read-endpoint body: every stage with its ordered slot/label pairs
    (the caller adds `version`)."""
    return [
        {"growth_stage": stage, "criteria": criteria}
        for stage, criteria in protocol_map.items()
    ]


def get_protocol_for_stage(protocol_map: ProtocolMap, stage: str | None) -> list[dict] | None:
    """The criteria for `stage`, or None when there's no stage or the stage
    has no protocol in this map (gated pass-through)."""
    if stage is None:
        return None
    return protocol_map.get(stage)


def build_snapshot(
    stage: str, scores: Mapping[str, int | None], protocol_map: ProtocolMap
) -> dict:
    """Freeze the protocol snapshot for a protocol `stage`, reading each
    slot's score from `scores` (keyed by snake_case RecordCreate attr).
    Raises ProtocolValidationError if `stage` isn't a protocol stage in the
    map, or any of the 4 scores is missing (None). Score RANGE (1-10) is
    enforced upstream by RecordCreate's field constraints."""
    criteria_defs = protocol_map.get(stage)
    if not criteria_defs:
        raise ProtocolValidationError(
            f"No inspection protocol for growth stage {stage!r}"
        )

    criteria: list[dict] = []
    missing: list[str] = []
    for c in criteria_defs:
        slot = c["slot"]
        attr = _SLOT_TO_ATTR[slot]
        score = scores.get(attr)
        if score is None:
            missing.append(slot)
        criteria.append({"slot": slot, "label": c["label"], "score": score})

    if missing:
        raise ProtocolValidationError(
            f"Inspection protocol for stage {stage!r} requires all 4 scores; "
            f"missing: {', '.join(missing)}"
        )

    return {
        "version": PROTOCOL_VERSION,
        "growthStage": stage,
        "criteria": criteria,
    }


def apply_protocol_snapshot(payload: RecordCreate, protocol_map: ProtocolMap) -> RecordCreate:
    """Return `payload` with any client-supplied protocol snapshot stripped
    from custom_fields, and — only when growth_stage is a protocol stage in
    `protocol_map` — a fresh server-built snapshot injected.

    Never trusts a client snapshot: SNAPSHOT_KEY is always removed first and
    re-added solely from server-side data. Raises ProtocolValidationError
    (→ 422 at the endpoint) when a protocol stage is missing a score."""
    custom = {k: v for k, v in payload.custom_fields.items() if k != SNAPSHOT_KEY}

    if get_protocol_for_stage(protocol_map, payload.growth_stage) is not None:
        assert payload.growth_stage is not None  # narrowed by the guard above
        custom[SNAPSHOT_KEY] = build_snapshot(
            payload.growth_stage,
            {
                "field_prep_score": payload.field_prep_score,
                "weather_score": payload.weather_score,
                "care_score": payload.care_score,
                "variety_resistance_score": payload.variety_resistance_score,
            },
            protocol_map,
        )

    return payload.model_copy(update={"custom_fields": custom})
