"""Round 50 — two production lenses, the concern strip, and three bugs.

The operator's rule, in their words: a plot counts as finished only when
its LAST record carries ผลผลิตหลังทำความสะอาด. Someone can mis-tap
"เก็บเกี่ยว" mid-season and keep inspecting afterwards, and closing a
cycle is a staff action that may never happen — so neither the stage nor
the cycle status can be the test. On this repo's own data the two
definitions of "finished" overlap in ZERO plots, which is why cycle status
became a filter instead.

Three things here exist because the first cut got them wrong against real
data, and each wrong answer looked plausible:

  * every record was summed, so a plot's target was counted once per visit
    — the kg totals came out 2.5x too big AND the percentage moved (77% vs
    93%), because the plots inspected most often are the ones furthest on.
  * a plot with no records at all could not appear in the follow-up list
    (that list is built from records). On UAT that hid 641 of 763 plots.
  * cycle_status reached every endpoint signature but one query never
    forwarded it, so filtering to 3 plots still returned 60 flagged ones.
"""
from __future__ import annotations

import datetime
import inspect
from decimal import Decimal

import pytest

from app.api.v1 import dashboard as dashboard_api
from app.repositories import dashboard_repository as repo
from app.schemas.dashboard import (
    CONCERN_FINISHED_LOW,
    CONCERN_IN_PRODUCTION_LOW,
    CONCERN_LOW_RETENTION,
    CONCERN_MISSING_CLEANED,
    ExecutiveSummary,
    ProductionStatus,
)

TODAY = datetime.date(2026, 10, 15)
KEY = "executive_dashboard.read"

_EXECUTIVE_QUERIES = (
    repo.get_executive_summary,
    repo.get_executive_trend,
    repo.get_executive_breakdown,
    repo.get_executive_follow_up,
    repo.get_production_status,
    repo.get_concerns,
)


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


class TestTheLensTest:
    def test_the_last_record_decides_not_the_cycle_status(self) -> None:
        src = inspect.getsource(repo.get_production_status)
        assert "_last_record_sq(" in src
        # The lens split keys off cleaned_kg on that row, nothing else.
        assert "cleaned_kg.is_(None)" in src
        assert "cleaned_kg.isnot(None)" in src

    def test_the_last_record_is_the_latest_by_visit_date(self) -> None:
        # A mis-tapped "เก็บเกี่ยว" followed by more inspections must leave
        # the plot in production: only the newest row is read, and ties fall
        # back to insert order so the result is never arbitrary.
        src = inspect.getsource(repo._last_record_sq)
        assert "distinct(Record.plot_id)" in src
        assert "Record.record_date.desc()" in src
        assert "Record.created_at.desc()" in src

    def test_cycle_status_is_nowhere_in_the_lens_test(self) -> None:
        # The two signals disagree completely on real data; letting status
        # in here would silently halve what the page can see.
        src = inspect.getsource(repo.get_production_status)
        body = src.split('"""')[-1]
        assert "CYCLE_STATUS_HARVESTED" not in body

    def test_both_lenses_divide_by_the_same_target(self) -> None:
        # What makes "คาดว่าจะได้ 90%" and "ผลิตได้จริง 76%" comparable at
        # all — and what the dumbbell chart depends on.
        src = inspect.getsource(repo.get_production_status)
        assert src.count("lr.c.target_kg") >= 2
        assert "lens_pct(g_yield, g_target)" in src
        assert "lens_pct(d_clean, d_target)" in src


class TestLensPct:
    def test_an_ordinary_result(self) -> None:
        assert repo.lens_pct(Decimal("90"), Decimal("100")) == 90.0

    @pytest.mark.parametrize("value,target", [
        (Decimal("50"), None), (Decimal("50"), Decimal("0")),
        (None, Decimal("100")), (Decimal("50"), Decimal("-1")),
    ])
    def test_nothing_to_compare_is_none_not_zero(self, value, target) -> None:
        assert repo.lens_pct(value, target) is None

    def test_over_plan_is_a_real_result(self) -> None:
        assert repo.lens_pct(Decimal("131"), Decimal("100")) == 131.0


class TestRetentionBase:
    def test_it_uses_the_finished_plots_own_harvest(self) -> None:
        # Not the window's whole harvest. Getting this wrong once already
        # turned "most plots have not reported a cleaned weight" into
        # "9% survived cleaning".
        src = inspect.getsource(repo.get_production_status)
        assert "retention_pct(d_clean, d_harvest)" in src
        assert "func.sum(lr.c.yield_kg).filter(done)" in src


class TestTheDoubleCountIsGone:
    def test_the_summary_reads_one_row_per_plot(self) -> None:
        src = inspect.getsource(repo.get_executive_summary)
        assert "_last_record_sq(" in src
        # The old shape summed the records table directly.
        assert "func.sum(Record.yield_target_kg_snapshot)" not in src
        assert "func.sum(Record.yield_quantity_kg)" not in src

    def test_the_unfilled_count_is_plots_not_records(self) -> None:
        # It sits beside cleaned_record_count, which is now a plot count
        # too; one of each would quietly not add up.
        src = inspect.getsource(repo.get_executive_summary)
        assert "plots_in_range - cleaned_count" in src


class TestNeverInspected:
    def test_it_outranks_every_other_reason(self) -> None:
        worst = repo.follow_up_sort_key(repo.FOLLOW_UP_NEVER, 1.0)
        for other in (repo.FOLLOW_UP_LOW_SCORE, repo.FOLLOW_UP_OVERDUE,
                      repo.FOLLOW_UP_LOW_YIELD):
            assert worst < repo.follow_up_sort_key(other, 999.0)

    def test_longer_waiting_comes_first(self) -> None:
        assert (repo.follow_up_sort_key(repo.FOLLOW_UP_NEVER, 300.0)
                < repo.follow_up_sort_key(repo.FOLLOW_UP_NEVER, 30.0))

    def test_it_is_found_without_going_through_the_records_table(self) -> None:
        # The whole point: a plot with zero records cannot be reached by a
        # query that starts FROM records, which is why it was invisible.
        src = inspect.getsource(repo._flag_sets)
        assert "~exists(" in src
        assert "Plot.created_at" in src

    def test_every_reason_has_a_severity_and_a_direction(self) -> None:
        assert set(repo._FOLLOW_UP_SEVERITY) == set(repo._FOLLOW_UP_DIRECTION)
        assert repo.FOLLOW_UP_NEVER in repo._FOLLOW_UP_SEVERITY
        assert len(set(repo._FOLLOW_UP_SEVERITY.values())) == 4


class TestCycleStatusFilter:
    def test_it_reads_the_latest_cycle_not_any_cycle(self) -> None:
        # A plot that closed last season and opened a new one is growing,
        # not closed.
        src = inspect.getsource(repo._latest_cycle_sq)
        assert "distinct(PlotCycle.plot_id)" in src
        assert "cycle_no.desc()" in src

    @pytest.mark.parametrize("fn", _EXECUTIVE_QUERIES)
    def test_every_query_accepts_it(self, fn) -> None:
        assert "cycle_status" in inspect.signature(fn).parameters

    @pytest.mark.parametrize("fn", [repo._flag_sets, repo._actionable_counts,
                                    repo._last_visit_sq, repo._last_record_sq,
                                    repo._plot_scope])
    def test_every_helper_underneath_accepts_it_too(self, fn) -> None:
        assert "cycle_status" in inspect.signature(fn).parameters

    def test_no_helper_call_silently_drops_it(self) -> None:
        """The bug this round shipped and then caught.

        Every signature took cycle_status, and one call inside _flag_sets
        still read `_last_visit_sq(supplier_id, p_code)`. Filtering to 3
        plots returned 60 flagged ones, because the overdue set was never
        narrowed. A parameter accepted and dropped is worse than one that
        does not exist.
        """
        for fn in (repo._flag_sets, repo.get_executive_summary,
                   repo.get_executive_breakdown, repo.get_executive_follow_up,
                   repo.get_production_status, repo.get_concerns):
            src = inspect.getsource(fn)
            for call in ("_last_visit_sq(supplier_id, p_code)",
                         "_last_record_sq(supplier_id, p_code)",
                         "_plot_scope(supplier_id, p_code)"):
                assert call + ")" not in src.replace(" ", "").replace("\n", "") \
                    or True  # readable form asserted below
            # The readable check: no two-argument call of the scoped helpers.
            compact = src.replace(" ", "").replace("\n", "")
            for helper in ("_last_visit_sq(", "_last_record_sq(", "_plot_scope("):
                for frag in (helper + "supplier_id,p_code)",):
                    assert frag not in compact, f"{fn.__name__} drops cycle_status"

    @pytest.mark.parametrize("path", [
        "/executive", "/executive/trend", "/executive/breakdown",
        "/executive/follow-up", "/executive/breakdown/export",
        "/executive/production", "/executive/concerns",
    ])
    def test_every_endpoint_offers_it(self, path) -> None:
        assert "cycle_status" in set(
            inspect.signature(_route(path).endpoint).parameters
        )

    def test_the_endpoints_forward_it(self) -> None:
        for name in ("get_executive", "get_executive_trend",
                     "get_executive_breakdown", "get_executive_follow_up",
                     "export_executive_breakdown", "get_production_status",
                     "get_concerns"):
            src = inspect.getsource(getattr(dashboard_api, name))
            assert "cycle_status=cycle_status" in src, name

    def test_it_has_no_default(self) -> None:
        # Closing a cycle is a staff action that may never happen; a page
        # defaulting to "harvested only" would show almost nothing.
        src = inspect.getsource(dashboard_api)
        assert "_CYCLE_STATUS_Q = Query(\n    None," in src


class TestConcerns:
    def test_the_threshold_is_seventy_and_travels_with_the_rows(self) -> None:
        assert repo.PRODUCTION_LOW_PCT == Decimal("70")
        from app.schemas.dashboard import ExecutiveConcerns
        assert "threshold_pct" in ExecutiveConcerns.model_fields

    def test_both_lenses_are_judged_on_the_same_threshold(self) -> None:
        src = inspect.getsource(repo.get_concerns)
        assert "(CONCERN_IN_PRODUCTION_LOW, growing, lr.c.yield_kg)" in src
        assert "(CONCERN_FINISHED_LOW, done, lr.c.cleaned_kg)" in src

    def test_the_data_gap_is_keyed_on_status_not_on_a_stage_name(self) -> None:
        # growth_stage is editable master data: an admin renaming
        # "เก็บเกี่ยว" would switch the check off with nothing to show for
        # it. Cycle status is an enum.
        src = inspect.getsource(repo.get_concerns)
        assert "CYCLE_STATUS_HARVESTED" in src
        # Comments stripped: the explanation for NOT using the stage name
        # mentions it, and tripping on prose would make this test useless.
        code = "".join(
            line.split("#")[0] for line in src.splitlines(keepends=True)
        )
        assert "growth_stage ==" not in code
        assert "เก็บเกี่ยว" not in code

    def test_retention_is_compared_against_this_pages_own_average(self) -> None:
        # What counts as normal depends on the crop, and the filters may
        # already have narrowed the page to one.
        src = inspect.getsource(repo.get_concerns)
        assert "avg_retention - got >= RETENTION_GAP_PTS" in src

    def test_rows_carry_data_not_a_sentence(self) -> None:
        from app.schemas.dashboard import ConcernRow
        fields = set(ConcernRow.model_fields)
        assert {"severity", "kind", "key", "plots", "value"} <= fields
        assert "message" not in fields

    def test_high_severity_sorts_first(self) -> None:
        src = inspect.getsource(repo.get_concerns)
        assert 'rows.sort(key=lambda r: (r.severity != "high", -r.plots))' in src

    @pytest.mark.parametrize("kind", [
        CONCERN_IN_PRODUCTION_LOW, CONCERN_FINISHED_LOW,
        CONCERN_MISSING_CLEANED, CONCERN_LOW_RETENTION,
    ])
    def test_every_kind_is_a_named_constant(self, kind) -> None:
        assert isinstance(kind, str) and kind


class TestEndpointGate:
    @pytest.mark.parametrize("path", ["/executive/production", "/executive/concerns"])
    def test_the_new_endpoints_are_behind_the_same_key(self, path) -> None:
        assert _permission_keys(_route(path)) == {KEY}


class TestSchemaContract:
    def test_the_production_payload_carries_both_lenses_and_the_stages(self) -> None:
        fields = set(ProductionStatus.model_fields)
        assert {"in_production", "finished", "finished_harvest_kg",
                "finished_retention_pct", "by_stage", "range"} <= fields

    def test_the_summary_still_echoes_its_range(self) -> None:
        assert "range" in ExecutiveSummary.model_fields

class TestSixMonthRange:
    """Round 55 — the operator asked for a 6-month preset."""

    def test_it_is_offered_alongside_the_others(self) -> None:
        assert repo.RANGE_6M == "6m"
        assert repo.EXECUTIVE_RANGES == ("30d", "3m", "6m", "ytd")

    def test_it_covers_half_a_year(self) -> None:
        w = repo.resolve_window("6m", None, None, TODAY)
        assert (TODAY - w.start).days == 180

    def test_it_buckets_by_month_not_week(self) -> None:
        # 26 weekly bars on one axis is unreadable; six monthly ones are not.
        assert repo.resolve_window("6m", None, None, TODAY).granularity == "month"

    def test_three_months_still_buckets_by_week(self) -> None:
        assert repo.resolve_window("3m", None, None, TODAY).granularity == "week"


class TestConcernThreshold:
    """Round 55 — the reader types the threshold instead of inheriting 70."""

    def test_the_default_is_still_seventy(self) -> None:
        assert repo.normalize_concern_pct(None) == Decimal("70")
        assert repo.PRODUCTION_LOW_PCT == Decimal("70")

    @pytest.mark.parametrize("given,expected", [
        (70, "70"), (50, "50"), ("85.5", "85.5"), (Decimal("99.9"), "99.9"),
    ])
    def test_a_usable_number_passes_through(self, given, expected) -> None:
        assert repo.normalize_concern_pct(given) == Decimal(expected)

    @pytest.mark.parametrize("given,expected", [
        (0, "1"), (-5, "1"), (500, "200"), (1000, "200"),
    ])
    def test_an_out_of_range_number_is_clamped_not_rejected(self, given, expected) -> None:
        # A number typed into a filter box must never produce an error page
        # on a read-only dashboard.
        assert repo.normalize_concern_pct(given) == Decimal(expected)

    @pytest.mark.parametrize("junk", ["abc", "", None, "NaN", object()])
    def test_junk_falls_back_to_the_default(self, junk) -> None:
        assert repo.normalize_concern_pct(junk) == Decimal("70")

    def test_zero_can_never_be_the_threshold(self) -> None:
        # It would report that nothing is wrong anywhere — the most
        # dangerous possible reading of this block.
        assert repo.normalize_concern_pct(0) > 0
        assert repo.CONCERN_PCT_MIN > 0

    def test_above_one_hundred_is_allowed(self) -> None:
        # "show me everything under 120% of plan" is a fair question.
        assert repo.normalize_concern_pct(120) == Decimal("120")

    def test_the_query_uses_the_given_threshold_for_both_lenses(self) -> None:
        import inspect
        src = inspect.getsource(repo.get_concerns)
        assert "threshold = normalize_concern_pct(threshold_pct)" in src
        assert "low = threshold / Decimal(100)" in src
        # and the payload reports what it actually used, not the constant
        assert "threshold_pct=float(threshold)" in src

    def test_the_endpoint_accepts_it(self) -> None:
        import inspect
        params = inspect.signature(_route("/executive/concerns").endpoint).parameters
        assert "threshold_pct" in params

    def test_the_endpoint_forwards_it(self) -> None:
        import inspect
        src = inspect.getsource(dashboard_api.get_concerns)
        assert "threshold_pct=threshold_pct" in src

    def test_no_other_endpoint_grew_a_threshold(self) -> None:
        # It belongs to this block alone; anywhere else it would change a
        # number nobody asked it to change.
        import inspect
        for path in ("/executive", "/executive/production", "/executive/breakdown"):
            params = inspect.signature(_route(path).endpoint).parameters
            assert "threshold_pct" not in params, path
