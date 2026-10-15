"""Round 47 — a custom date window, a supplier filter, and an .xlsx export.

"ทั้งหมด" is gone. It answered a question nobody asks ("since the beginning
of the data") and got slower as the table grew; what people actually wanted
was "that season", which needs two dates.

The filters are the dangerous part: a page that narrows SOME of its numbers
to the chosen supplier and leaves the rest company-wide is worse than one
that ignores the filter entirely, because it looks like it worked.
"""
from __future__ import annotations

import datetime
import zipfile
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest

from app.api.v1 import dashboard as dashboard_api
from app.repositories import dashboard_repository as repo
from app.schemas.dashboard import BreakdownRow, ExecutiveBreakdown

TODAY = datetime.date(2026, 10, 15)
KEY = "executive_dashboard.read"


def _route(path: str, method: str = "GET"):
    for r in dashboard_api.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def _permission_keys(route) -> set[str]:
    keys: set[str] = set()
    for dep in route.dependencies:
        closure = getattr(dep.dependency, "__closure__", None) or ()
        for cell in closure:
            if isinstance(cell.cell_contents, str) and "." in cell.cell_contents:
                keys.add(cell.cell_contents)
    return keys


def _params(route) -> set[str]:
    import inspect
    return set(inspect.signature(route.endpoint).parameters)


class TestAllIsGone:
    def test_the_unbounded_range_is_no_longer_offered(self) -> None:
        assert "all" not in repo.EXECUTIVE_RANGES
        assert repo.EXECUTIVE_RANGES == ("30d", "3m", "ytd")

    def test_an_old_bookmark_asking_for_it_still_renders(self) -> None:
        # Links to ?range=all exist in people's history. They get the
        # default window rather than an error page.
        w = repo.resolve_window("all", None, None, TODAY)
        assert w.key == repo.DEFAULT_RANGE

    def test_every_preset_now_has_a_start(self) -> None:
        # Nothing scans the whole table any more.
        for key in repo.EXECUTIVE_RANGES:
            assert repo.resolve_window(key, None, None, TODAY).start is not None


class TestCustomWindow:
    def test_two_dates_beat_the_preset(self) -> None:
        w = repo.resolve_window(
            "30d", datetime.date(2026, 6, 1), datetime.date(2026, 8, 31), TODAY,
        )
        assert w.key == "custom"
        assert w.start == datetime.date(2026, 6, 1)
        assert w.end == datetime.date(2026, 8, 31)

    def test_a_start_alone_is_still_a_custom_window(self) -> None:
        # "from 1 June" is a sentence a person means. Falling back to the
        # preset here would show them a window they did not ask for.
        w = repo.resolve_window("3m", datetime.date(2026, 6, 1), None, TODAY)
        assert w.key == "custom"
        assert w.start == datetime.date(2026, 6, 1)
        assert w.end is None

    def test_an_end_alone_is_too(self) -> None:
        w = repo.resolve_window("3m", None, datetime.date(2026, 6, 1), TODAY)
        assert w.key == "custom"
        assert w.start is None
        assert w.end == datetime.date(2026, 6, 1)

    def test_dates_typed_backwards_are_honoured_not_rejected(self) -> None:
        # Two date inputs make this easy to do and the intent is never in
        # doubt. Returning an empty page instead would just look broken.
        w = repo.resolve_window(
            None, datetime.date(2026, 8, 31), datetime.date(2026, 6, 1), TODAY,
        )
        assert (w.start, w.end) == (datetime.date(2026, 6, 1), datetime.date(2026, 8, 31))

    def test_a_short_custom_window_buckets_by_week(self) -> None:
        w = repo.resolve_window(
            None, datetime.date(2026, 6, 1), datetime.date(2026, 8, 31), TODAY,
        )
        assert w.granularity == "week"

    def test_a_long_custom_window_buckets_by_month(self) -> None:
        # 26 weekly bars on one axis is unreadable.
        w = repo.resolve_window(
            None, datetime.date(2026, 1, 1), datetime.date(2026, 12, 31), TODAY,
        )
        assert w.granularity == "month"

    def test_a_single_day_does_not_crash_the_span_maths(self) -> None:
        day = datetime.date(2026, 7, 4)
        w = repo.resolve_window(None, day, day, TODAY)
        assert w.granularity == "week"


class TestWindowConditions:
    def test_a_preset_bounds_only_the_start(self) -> None:
        assert len(repo.resolve_window("3m", None, None, TODAY).conditions()) == 1

    def test_a_closed_custom_window_bounds_both_ends(self) -> None:
        w = repo.resolve_window(
            None, datetime.date(2026, 6, 1), datetime.date(2026, 8, 31), TODAY,
        )
        assert len(w.conditions()) == 2

    def test_an_open_window_bounds_one_end(self) -> None:
        w = repo.resolve_window(None, datetime.date(2026, 6, 1), None, TODAY)
        assert len(w.conditions()) == 1


class TestSupplierScope:
    def test_no_supplier_means_only_the_active_filter(self) -> None:
        assert len(repo._plot_scope(None)) == 1
        assert len(repo._record_scope(None)) == 1

    def test_a_supplier_adds_exactly_one_condition_on_each_side(self) -> None:
        sid = uuid4()
        assert len(repo._plot_scope(sid)) == 2
        assert len(repo._record_scope(sid)) == 2

    def test_records_are_filtered_on_their_own_column(self) -> None:
        """The supplier filter never reaches through the plots table.

        records.supplier_id is forced equal to the plot's at create time, so
        the record side already carries the answer and the two filters
        cannot disagree.

        Round 49 note: PlotCycle DOES appear in here, for the P.Code filter
        — P.Code lives on the cycle and nowhere else. That is a subquery on
        plot_cycles, not a reach into plots, which is why this asserts on
        `Plot.` specifically rather than on the substring "Plot".
        """
        import inspect
        body = inspect.getsource(repo._record_scope).split('"""')[-1]
        assert "Record.supplier_id" in body
        assert "Plot." not in body

    @pytest.mark.parametrize("fn", [
        repo.get_executive_summary, repo.get_executive_trend,
        repo.get_executive_breakdown, repo.get_executive_follow_up,
    ])
    def test_every_query_accepts_the_supplier_filter(self, fn) -> None:
        # A page that narrows some blocks and not others is worse than one
        # that ignores the filter, because it looks like it worked.
        import inspect
        assert "supplier_id" in inspect.signature(fn).parameters


class TestEndpointParameters:
    @pytest.mark.parametrize("path", [
        "/executive", "/executive/trend", "/executive/breakdown",
        "/executive/breakdown/export",
    ])
    def test_the_period_filters_are_spelled_the_same_everywhere(self, path) -> None:
        # Same names the report endpoints already use, so the whole app asks
        # for a filtered period one way.
        assert {"range", "date_from", "date_to", "supplier_id"} <= _params(_route(path))

    def test_the_follow_up_list_takes_a_supplier_but_no_period(self) -> None:
        params = _params(_route("/executive/follow-up"))
        assert "supplier_id" in params
        assert "date_from" not in params and "range" not in params

    @pytest.mark.parametrize("path", [
        "/executive", "/executive/trend", "/executive/breakdown",
        "/executive/follow-up", "/executive/breakdown/export",
    ])
    def test_every_endpoint_including_the_export_is_behind_the_key(self, path) -> None:
        assert _permission_keys(_route(path)) == {KEY}


def _breakdown(group_by: str = "supplier") -> ExecutiveBreakdown:
    return ExecutiveBreakdown(group_by=group_by, range="3m", rows=[
        BreakdownRow(
            key="SUP005", label="Supplier Five", plots=80, plots_inspected=12,
            plots_overdue=41, inspections=30, avg_score=5.2,
            target_kg=Decimal("1000.00"), harvest_kg=Decimal("520.00"),
            achievement_pct=52.0,
        ),
        BreakdownRow(
            key="(ไม่ระบุ)", label="(ไม่ระบุ)", plots=3, plots_inspected=0,
            plots_overdue=0, inspections=0, avg_score=None,
            target_kg=None, harvest_kg=None, achievement_pct=None,
        ),
    ])


def _sheet_xml(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        return "".join(
            zf.read(n).decode("utf-8") for n in zf.namelist()
            if n.endswith(".xml")
        )


class TestExcelExport:
    def test_it_produces_a_real_workbook(self) -> None:
        content = dashboard_api._breakdown_workbook(_breakdown())
        assert content[:2] == b"PK"
        with zipfile.ZipFile(BytesIO(content)) as zf:
            assert "xl/worksheets/sheet1.xml" in zf.namelist()

    def test_the_header_row_matches_the_on_screen_table(self) -> None:
        # A workbook carrying MORE than the page is how a figure nobody has
        # looked at ends up in a meeting.
        xml = _sheet_xml(dashboard_api._breakdown_workbook(_breakdown()))
        for header in ("Supplier", "แปลง", "ตรวจแล้ว", "ค้างตรวจ", "คะแนนเฉลี่ย",
                       "เป้าผลิต (kg)", "เก็บได้ (kg)", "บรรลุเป้า (%)"):
            assert header in xml, header

    @pytest.mark.parametrize("group,header", [
        ("supplier", "Supplier"), ("crop", "พืช"), ("variety", "พันธุ์"),
    ])
    def test_the_first_column_is_named_after_the_grouping(self, group, header) -> None:
        xml = _sheet_xml(dashboard_api._breakdown_workbook(_breakdown(group)))
        assert header in xml

    def test_the_rows_are_there(self) -> None:
        xml = _sheet_xml(dashboard_api._breakdown_workbook(_breakdown()))
        assert "Supplier Five" in xml
        assert "(ไม่ระบุ)" in xml

    def test_an_absent_figure_is_a_blank_cell_not_a_zero(self) -> None:
        """0 in a spreadsheet is a measurement; these are "not measured".

        The second fixture row has None for score, target, harvest and
        achievement — columns E to H. Those cells must be absent from the
        sheet entirely, while its REAL zeros (plots_inspected and
        plots_overdue, columns C and D) must still be written as 0. A blank
        is also what AVERAGE() skips once a reader adds their own total row.
        """
        xml = _sheet_xml(dashboard_api._breakdown_workbook(_breakdown()))
        for col in ("E3", "F3", "G3", "H3"):
            assert f'r="{col}"' not in xml, f"{col} should be blank, not 0"
        for col in ("C3", "D3"):
            assert f'r="{col}"' in xml, f"{col} is a real zero and must be written"
        for col in ("A2", "B2", "C2", "D2", "E2", "F2", "G2", "H2"):
            assert f'r="{col}"' in xml, col

    def test_the_filename_names_the_grouping(self) -> None:
        import inspect
        src = inspect.getsource(dashboard_api.export_executive_breakdown)
        assert "executive-breakdown-" in src
        assert "result.group_by" in src

    def test_the_export_reuses_the_table_query_rather_than_its_own(self) -> None:
        # The download must never disagree with what the person was looking
        # at when they clicked it.
        import inspect
        src = inspect.getsource(dashboard_api.export_executive_breakdown)
        assert "repo.get_executive_breakdown(" in src

    def test_it_uses_the_one_shared_workbook_response(self) -> None:
        # Cache-Control: no-store lives in that helper; a second copy of the
        # response shape is a copy that can quietly lose it.
        import inspect
        src = inspect.getsource(dashboard_api.export_executive_breakdown)
        assert "xlsx_response(" in src
