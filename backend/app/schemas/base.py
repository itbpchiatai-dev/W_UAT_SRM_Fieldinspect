"""CamelBaseModel — Pydantic base for every request/response schema.

DB columns are snake_case (PostgreSQL convention) and API JSON keys are
camelCase (JS/TS convention). This class wires `alias_generator=to_camel`
+ `populate_by_name=True` so a single class definition can read snake_case
from ORM objects AND emit camelCase JSON.

Every schema under app/schemas/ MUST inherit from this class.
scripts/checks/camel_base_model_audit.py enforces it at pre-commit.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelBaseModel(BaseModel):
    """Base class: snake_case attrs in Python, camelCase keys in JSON."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
