"""Round 56 — the two lists page by ten, and each one downloads in full.

"สิ่งที่ต้องกังวล" and "แปลงที่ต้องตามงาน" were the two blocks that could
grow without bound: UAT alone has 641 never-inspected plots. They now come
back a page at a time with the full count alongside, and each gets an Excel
export that carries EVERY row rather than the page on screen.
"""
from __future__ import annotations

import datetime
import inspect
import zipfile
from io import BytesIO
from uuid import uuid4

import pytest

from app.api.v1 import dashboard as dashboard_api
from app.repositories import dashboard_repository as repo
from app.schemas.dashboard import (
    ConcernRow,
    ExecutiveConcerns,
    ExecutiveFollowUp,
    FollowUpRow,
)

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


def _params(route) -> dict[str, inspect.Parameter]:
    return dict(inspect.signature(route.endpoint).parameters)


def _sheet_xml(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        return "".join(
            zf.read(n).decode("utf-8") for n in zf.namelist()
            if n.endswith(".xml")
        )


# --- the slicer both lists share ---------------------------------------

class TestPaginate:
    ROWS = list(range(23))

    def test_the_first_page(self) -> None:
        assert repo.paginate(self.ROWS, 10, 0) == list(range(10))

    def test_the_second_page_continues_the_first(self) -> None:
        assert repo.paginate(self.ROWS, 10, 10) == list(range(10, 20))

    def test_the_last_page_is_short_not_padded(self) -> None:
        assert repo.paginate(self.ROWS, 10, 20) == [20, 21, 22]

    def test_past_the_end_is_empty_not_an_error(self) -> None:
        # Rows can disappear between two clicks; the page must still render.
        assert repo.paginate(self.ROWS, 10, 40) == []

    def test_no_limit_means_every_row(self) -> None:
        # What the exports ask for.
        assert repo.paginate(self.ROWS, None, 0) == self.ROWS

    def test_no_limit_still_honours_the_offset(self) -> None:
        assert repo.paginate(self.ROWS, None, 20) == [20, 21, 22]

    def test_a_negative_offset_is_the_first_page_not_the_tail(self) -> None:
        # Python's own slicing would return the LAST rows for -3 — a page
        # that quietly shows the least urgent plots as if they were first.
        assert repo.paginate(self.ROWS, 10, -3) == list(range(10))

    def test_it_does_not_touch_the_list_it_was_given(self) -> None:
        rows = [3, 1, 2]
        repo.paginate(rows, 1, 0)
        assert rows == [3, 1, 2]


# --- follow-up ----------------------------------------------------------

class TestFollowUpPaging:
    def test_it_ranks_everything_before_cutting_the_page(self) -> None:
        # Cutting first would make page 2 the second-worst ten of a random
        # ten, not the 11th-20th worst plots.
        src = inspect.getsource(repo.get_executive_follow_up)
        assert src.index("sorted(") < src.index("paginate(ranked, limit, offset)")

    def test_total_is_the_whole_list(self) -> None:
        src = inspect.getsource(repo.get_executive_follow_up)
        assert src.index("total = len(worst)") < src.index("paginate(")

    def test_only_the_page_is_looked_up(self) -> None:
        src = inspect.getsource(repo.get_executive_follow_up)
        assert "wanted = [plot_id for plot_id, _ in ordered]" in src
        assert "Plot.id.in_(wanted)" in src

    def test_paging_past_the_end_skips_the_lookup(self) -> None:
        src = inspect.getsource(repo.get_executive_follow_up)
        assert "if not ordered:" in src

    def test_the_endpoint_takes_an_offset(self) -> None:
        p = _params(_route("/executive/follow-up"))
        assert "offset" in p and "limit" in p
        assert p["offset"].default.default == 0

    def test_the_endpoint_forwards_it(self) -> None:
        src = inspect.getsource(dashboard_api.get_executive_follow_up)
        assert "offset=offset" in src

    def test_the_default_page_is_still_ten(self) -> None:
        assert _params(_route("/executive/follow-up"))["limit"].default.default == 10


# --- concerns -----------------------------------------------------------

class TestConcernsPaging:
    def test_it_ranks_everything_before_cutting_the_page(self) -> None:
        src = inspect.getsource(repo.get_concerns)
        assert src.index("rows.sort(") < src.index("paginate(rows, limit, offset)")

    def test_total_counts_before_the_cut(self) -> None:
        src = inspect.getsource(repo.get_concerns)
        assert "total=len(rows)" in src

    def test_the_payload_carries_the_total(self) -> None:
        payload = ExecutiveConcerns(threshold_pct=70, total=14, rows=[])
        assert payload.model_dump(by_alias=True)["total"] == 14

    def test_the_endpoint_takes_limit_and_offset(self) -> None:
        p = _params(_route("/executive/concerns"))
        assert "limit" in p and "offset" in p

    def test_without_a_limit_it_still_returns_every_row(self) -> None:
        # The response a caller got before round 56 — anything that does
        # not ask for a page must not start receiving one.
        assert _params(_route("/executive/concerns"))["limit"].default.default is None

    def test_the_endpoint_forwards_both(self) -> None:
        src = inspect.getsource(dashboard_api.get_concerns)
        assert "limit=limit, offset=offset" in src


# --- the exports --------------------------------------------------------

_EXPORTS = {
    "/executive/concerns/export": ("/executive/concerns", "export_concerns",
                                   "repo.get_concerns("),
    "/executive/follow-up/export": ("/executive/follow-up", "export_follow_up",
                                    "repo.get_executive_follow_up("),
}


class TestExportRoutes:
    @pytest.mark.parametrize("path", sorted(_EXPORTS))
    def test_it_is_behind_the_same_key_as_the_page(self, path) -> None:
        assert _permission_keys(_route(path)) == {KEY}

    @pytest.mark.parametrize("path", sorted(_EXPORTS))
    def test_it_takes_the_list_filters_but_no_page(self, path) -> None:
        # Same filters, so the file matches the list it came from; no page,
        # because the file is all of it.
        list_path, _fn, _call = _EXPORTS[path]
        expected = set(_params(_route(list_path))) - {"limit", "offset"}
        assert set(_params(_route(path))) == expected

    @pytest.mark.parametrize("path", sorted(_EXPORTS))
    def test_it_reuses_the_list_query_and_asks_for_every_row(self, path) -> None:
        _list_path, fn, call = _EXPORTS[path]
        src = inspect.getsource(getattr(dashboard_api, fn))
        assert call in src
        assert "limit=None" in src

    @pytest.mark.parametrize("path", sorted(_EXPORTS))
    def test_it_uses_the_one_shared_workbook_response(self, path) -> None:
        _list_path, fn, _call = _EXPORTS[path]
        assert "xlsx_response(" in inspect.getsource(getattr(dashboard_api, fn))

    def test_the_follow_up_export_has_no_period_like_the_list(self) -> None:
        p = _params(_route("/executive/follow-up/export"))
        assert not {"range", "date_from", "date_to"} & set(p)

    def test_the_concerns_export_keeps_the_threshold(self) -> None:
        # Without it the file would list a different set of P.Codes from
        # the screen whenever someone had typed a number other than 70.
        src = inspect.getsource(dashboard_api.export_concerns)
        assert "threshold_pct=threshold_pct" in src


def _concerns() -> ExecutiveConcerns:
    return ExecutiveConcerns(threshold_pct=75.0, total=3, rows=[
        ConcernRow(severity="high", kind=repo.CONCERN_IN_PRODUCTION_LOW,
                   key="WM-141", plots=4, value=59.3),
        ConcernRow(severity="medium", kind=repo.CONCERN_MISSING_CLEANED,
                   key="CU-022", plots=2, value=None),
        ConcernRow(severity="medium", kind=repo.CONCERN_LOW_RETENTION,
                   key="(ไม่ระบุ)", plots=1, value=61.0),
    ])


class TestConcernsWorkbook:
    def test_it_produces_a_real_workbook(self) -> None:
        content = dashboard_api._concerns_workbook(_concerns())
        assert content[:2] == b"PK"

    def test_the_headers(self) -> None:
        xml = _sheet_xml(dashboard_api._concerns_workbook(_concerns()))
        for header in ("ระดับ", "P.Code", "ประเด็น", "แปลง", "ค่า (%)",
                       "เกณฑ์ (% ของเป้า)"):
            assert header in xml, header

    def test_every_row_in_words_the_page_uses(self) -> None:
        xml = _sheet_xml(dashboard_api._concerns_workbook(_concerns()))
        for text in ("รุนแรง", "เฝ้าระวัง", "WM-141", "CU-022", "(ไม่ระบุ)",
                     "ยังไม่กรอกผลผลิตหลังทำความสะอาด"):
            assert text in xml, text

    def test_every_kind_has_a_thai_label(self) -> None:
        for kind in (repo.CONCERN_IN_PRODUCTION_LOW, repo.CONCERN_FINISHED_LOW,
                     repo.CONCERN_MISSING_CLEANED, repo.CONCERN_LOW_RETENTION):
            assert kind in dashboard_api._CONCERN_TEXT, kind

    def test_the_threshold_appears_only_where_it_was_applied(self) -> None:
        # Row 2 (in_production_low) was tested against 75; rows 3 and 4 were
        # not, and a 75 beside them would claim a test that never ran.
        xml = _sheet_xml(dashboard_api._concerns_workbook(_concerns()))
        assert 'r="F2"' in xml
        assert 'r="F3"' not in xml and 'r="F4"' not in xml

    def test_a_missing_value_is_a_blank_cell_not_a_zero(self) -> None:
        xml = _sheet_xml(dashboard_api._concerns_workbook(_concerns()))
        assert 'r="E3"' not in xml        # missing_cleaned carries no value
        assert 'r="E2"' in xml and 'r="E4"' in xml


def _follow_up() -> ExecutiveFollowUp:
    row = dict(plot_id=uuid4(), plot_name="แปลงทดสอบ", supplier_code="SUP001")
    return ExecutiveFollowUp(total=2, rows=[
        FollowUpRow(**row, plot_code="P-0001", reason=repo.FOLLOW_UP_NEVER,
                    value=212.0, last_inspected=None),
        FollowUpRow(**row, plot_code="P-0002", reason=repo.FOLLOW_UP_OVERDUE,
                    value=45.0, last_inspected=datetime.date(2026, 8, 31)),
    ])


class TestFollowUpWorkbook:
    def test_it_produces_a_real_workbook(self) -> None:
        assert dashboard_api._follow_up_workbook(_follow_up())[:2] == b"PK"

    def test_the_headers(self) -> None:
        xml = _sheet_xml(dashboard_api._follow_up_workbook(_follow_up()))
        for header in ("รหัสแปลง", "ชื่อแปลง", "Supplier", "เหตุผล", "ค่า",
                       "หน่วย", "ตรวจล่าสุด"):
            assert header in xml, header

    def test_the_rows(self) -> None:
        xml = _sheet_xml(dashboard_api._follow_up_workbook(_follow_up()))
        for text in ("P-0001", "P-0002", "SUP001", "ไม่เคยตรวจ", "ค้างตรวจ",
                     "2026-08-31", "ยังไม่เคยตรวจ"):
            assert text in xml, text

    def test_the_unit_says_what_the_number_counts(self) -> None:
        # 212 and 45 are both days, but from different starting points; a
        # bare number column would read them as the same thing.
        xml = _sheet_xml(dashboard_api._follow_up_workbook(_follow_up()))
        assert "นับจากลงทะเบียนแปลง" in xml
        assert "นับจากตรวจล่าสุด" in xml

    @pytest.mark.parametrize("reason", [
        repo.FOLLOW_UP_NEVER, repo.FOLLOW_UP_LOW_SCORE,
        repo.FOLLOW_UP_OVERDUE, repo.FOLLOW_UP_LOW_YIELD,
    ])
    def test_every_reason_has_a_label_and_a_unit(self, reason) -> None:
        assert reason in dashboard_api._REASON_TEXT
        assert reason in dashboard_api._REASON_UNIT

    def test_the_labels_match_the_page(self) -> None:
        # ExecutiveDashboard.tsx REASON_TEXT — the badge on screen and the
        # cell in the file must say the same thing.
        assert dashboard_api._REASON_TEXT == {
            "never_inspected": "ไม่เคยตรวจ",
            "low_score": "คะแนนต่ำ",
            "overdue": "ค้างตรวจ",
            "low_yield": "ผลผลิตต่ำกว่าเป้า",
        }
