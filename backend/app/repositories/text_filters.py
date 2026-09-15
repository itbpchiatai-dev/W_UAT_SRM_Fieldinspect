"""Free-text list filters shared by more than one repository (round X).

One rule for "the user typed part of a reference": the Plots list's "เลขที่
Invoice" box (round O) and both reports' invoice filter must agree on what a
search for "INV-26" finds, so the rule lives here once instead of being
re-spelled per query.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement

_ESCAPE = "\\"


def contains_text(column, raw: str | None) -> ColumnElement[bool] | None:
    """A trimmed, case-insensitive substring match on `column`, or None when
    `raw` is empty or only spaces (the caller then adds no filter at all).

    `%` and `_` in the user's text are matched LITERALLY. Left unescaped, a
    search for "INV_1" would also match "INVX1" and a "%" would match anything
    — a filter that quietly widens itself. Round O's first version did not
    escape; this is where that is fixed for every caller."""
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    escaped = (
        trimmed.replace(_ESCAPE, _ESCAPE * 2).replace("%", _ESCAPE + "%").replace("_", _ESCAPE + "_")
    )
    return column.ilike(f"%{escaped}%", escape=_ESCAPE)
