"""The Supplier reads a P.Code where the internal reader reads a variety
(round 30).

Round 29 built the mechanism and left the policy empty. This round fills it in
for the first column: the variety is hidden from both Supplier reports, and the
P.Code stands in its place.

They are not two different facts. Since round Y the variety is a DETAIL of the
P.Code — master data runs crop → variety → p_code, a P.Code belongs to exactly
one variety, and a plot is registered by picking the crop and the P.Code — so
"CTT-507" names the variety by the name the purchase order, the Lot No and the
Supplier's own paperwork already use.

What makes the change small is that neither report had to gain a column: both
already carry p_code, and both already populate it from the row's own cycle.
Only the variety leaves.
"""
from __future__ import annotations

import pytest

from app.api.v1 import reports as reports_api
from app.schemas.report import ReportCycleYieldRow, ReportPlotStatusRow

# (the Supplier variant's hidden set, its report's columns, the row model,
#  the field that goes, the field that stands in for it)
VARIANTS = [
    pytest.param(
        reports_api.SUPPLIER_HIDDEN_PLOT_STATUS,
        reports_api.PLOT_STATUS_COLUMNS,
        ReportPlotStatusRow,
        "current_variety",
        "p_code",
        id="plot-status",
    ),
    pytest.param(
        reports_api.SUPPLIER_HIDDEN_CYCLE_YIELD,
        reports_api.CYCLE_YIELD_COLUMNS,
        ReportCycleYieldRow,
        "variety",
        "p_code",
        id="cycle-yield",
    ),
]


@pytest.mark.parametrize("hidden,columns,model,gone,shown", VARIANTS)
def test_the_variety_is_hidden_and_the_p_code_is_not(
    hidden, columns, model, gone, shown,
) -> None:
    assert gone in hidden
    assert shown not in hidden
    # Both are real row fields, so response_model_exclude actually bites and
    # the P.Code is really there to take the variety's place.
    assert gone in model.model_fields
    assert shown in model.model_fields


@pytest.mark.parametrize("hidden,columns,model,gone,shown", VARIANTS)
def test_the_workbook_loses_the_variety_column_and_keeps_the_p_code(
    hidden, columns, model, gone, shown,
) -> None:
    """The set drives the spreadsheet as well as the JSON — a variety that
    vanished from the screen but rode out in the download is the exact failure
    round 29's one-place column spec exists to prevent."""
    internal = reports_api._visible(columns, frozenset())
    supplier = reports_api._visible(columns, hidden)

    assert gone in [c.key for c in internal]
    assert gone not in [c.key for c in supplier]
    assert shown in [c.key for c in supplier]
    # Exactly one column left — nothing else was caught by the same edit.
    assert len(supplier) == len(internal) - 1


@pytest.mark.parametrize("hidden,columns,model,gone,shown", VARIANTS)
def test_the_variety_header_is_gone_from_the_supplier_workbook(
    hidden, columns, model, gone, shown,
) -> None:
    """Headers, not just keys: the reader sees the header row, and a sheet that
    still says "พันธุ์" over the P.Code column would be worse than either."""
    headers = [c.header for c in reports_api._visible(columns, hidden)]
    assert "พันธุ์" not in headers
    assert "P.Code" in headers


@pytest.mark.parametrize("hidden,columns,model,gone,shown", VARIANTS)
def test_the_internal_report_is_untouched(
    hidden, columns, model, gone, shown,
) -> None:
    """The whole point of two variants: this round changes what a Supplier
    sees and nothing about what an Admin sees."""
    headers = [c.header for c in reports_api._visible(columns, frozenset())]
    assert "พันธุ์" in headers
    assert "P.Code" in headers


@pytest.mark.parametrize("path,name", [
    ("/supplier/plot-status", "SUPPLIER_HIDDEN_PLOT_STATUS"),
    ("/supplier/cycle-yield", "SUPPLIER_HIDDEN_CYCLE_YIELD"),
])
def test_the_supplier_json_route_really_excludes_it(path: str, name: str) -> None:
    """Reading the set is not the same as the route using it: this asserts the
    live route object carries the exclusion, so the JSON a Supplier's browser
    receives has no variety key in it at all."""
    route = next(
        r for r in reports_api.router.routes
        if getattr(r, "path", None) == path and "GET" in getattr(r, "methods", set())
    )
    assert route.response_model_exclude == getattr(reports_api, name)
    assert route.response_model_exclude


def test_the_p_code_is_populated_not_just_declared() -> None:
    """A p_code column that always read blank would satisfy every test above
    and still show the Supplier nothing. Both report rows are built from the
    cycle's own p_code — asserted against the repository's source so it cannot
    quietly become a default-None field again."""
    import inspect

    from app.repositories import report_repository

    src = inspect.getsource(report_repository)
    assert src.count("p_code=cycle.p_code") == 2
