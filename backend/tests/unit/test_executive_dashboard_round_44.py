"""Round 44 — the time range, the comparison table, and the follow-up list.

Two of these tests exist because the first version of this round got the
answer wrong against real data, and both wrong answers looked plausible on
screen:

  * cleaned_retention_pct divided by EVERY record's harvest instead of the
    harvest of the records that also reported a cleaned weight. It printed
    9%, which reads as "91% was lost in cleaning" rather than "most records
    have not filled this in".
  * the follow-up list negated every reason's value when sorting, so a
    condition score of 3 was listed above a score of 2.

Neither would have been caught by "does the endpoint return 200".
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.api.v1 import dashboard as dashboard_api
from app.repositories import dashboard_repository as repo

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


class TestRangeWindow:
    # Round 47 replaced range_start/range_granularity with resolve_window,
    # which also carries the custom from/to case. These keep asserting what
    # the presets MEAN; the new API is covered in round 47's own file.

    @pytest.mark.parametrize("key,expected_days", [("30d", 30), ("3m", 90)])
    def test_the_rolling_windows_are_what_their_names_say(self, key, expected_days) -> None:
        w = repo.resolve_window(key, None, None, TODAY)
        assert (TODAY - w.start).days == expected_days

    def test_year_to_date_starts_on_the_first_of_january(self) -> None:
        assert repo.resolve_window("ytd", None, None, TODAY).start == datetime.date(2026, 1, 1)

    @pytest.mark.parametrize("bad", [None, "", "90d", "last-week", "ALL", "3M"])
    def test_an_unknown_range_falls_back_instead_of_failing(self, bad) -> None:
        # A read-only dashboard reached from a stale bookmark should render
        # the default page, not an error panel.
        assert repo.normalize_range(bad) == repo.DEFAULT_RANGE

    @pytest.mark.parametrize("key", repo.EXECUTIVE_RANGES)
    def test_every_supported_range_survives_normalisation(self, key) -> None:
        assert repo.normalize_range(key) == key

    def test_short_ranges_bucket_by_week_and_long_ones_by_month(self) -> None:
        # 30 days grouped by month is one bar, which is not a trend.
        def gran(key: str) -> str:
            return repo.resolve_window(key, None, None, TODAY).granularity

        assert gran("30d") == "week"
        assert gran("3m") == "week"
        assert gran("ytd") == "month"


class TestRetentionPct:
    def test_it_divides_by_the_harvest_of_the_same_records(self) -> None:
        assert repo.retention_pct(Decimal("875"), Decimal("1000")) == 87.5

    def test_the_call_site_passes_the_filtered_harvest_not_the_window_total(self) -> None:
        # THE bug. Both arguments are Decimals of the same shape, so only the
        # variable NAME at the call site distinguishes right from wrong —
        # which is exactly why it is asserted here.
        import inspect

        src = inspect.getsource(repo.get_executive_summary)
        assert "retention_pct(cleaned_kg, harvest_kg_of_cleaned)" in src
        assert "retention_pct(cleaned_kg, harvest_kg)" not in src

    @pytest.mark.parametrize("cleaned,base", [
        (Decimal("500"), None),          # nobody reported a cleaned weight
        (Decimal("500"), Decimal("0")),  # and nothing to divide by
        (None, Decimal("1000")),
    ])
    def test_nothing_to_compare_is_none_not_zero(self, cleaned, base) -> None:
        assert repo.retention_pct(cleaned, base) is None

    def test_cleaning_cannot_normally_add_weight_but_is_not_capped(self) -> None:
        # Over 100% means a data-entry error worth SEEING, not hiding.
        assert repo.retention_pct(Decimal("1100"), Decimal("1000")) == 110.0


class TestFollowUpOrder:
    def test_a_confirmed_low_score_outranks_an_unknown(self) -> None:
        # A bad score is a problem somebody already saw; overdue is a gap in
        # knowledge; a missed target is history.
        score = repo.follow_up_sort_key(repo.FOLLOW_UP_LOW_SCORE, 3.0)
        overdue = repo.follow_up_sort_key(repo.FOLLOW_UP_OVERDUE, 200.0)
        low_yield = repo.follow_up_sort_key(repo.FOLLOW_UP_LOW_YIELD, 10.0)
        assert score < overdue < low_yield

    def test_a_lower_score_comes_first(self) -> None:
        # THE other bug: every reason used to be sorted descending, which
        # put a score of 3 above a score of 2.
        assert (repo.follow_up_sort_key(repo.FOLLOW_UP_LOW_SCORE, 1.0)
                < repo.follow_up_sort_key(repo.FOLLOW_UP_LOW_SCORE, 3.0))

    def test_more_days_overdue_comes_first(self) -> None:
        assert (repo.follow_up_sort_key(repo.FOLLOW_UP_OVERDUE, 98.0)
                < repo.follow_up_sort_key(repo.FOLLOW_UP_OVERDUE, 31.0))

    def test_a_worse_harvest_comes_first(self) -> None:
        assert (repo.follow_up_sort_key(repo.FOLLOW_UP_LOW_YIELD, 41.0)
                < repo.follow_up_sort_key(repo.FOLLOW_UP_LOW_YIELD, 79.0))

    def test_sorting_a_mixed_list_puts_the_worst_of_each_reason_on_top(self) -> None:
        items = [
            (repo.FOLLOW_UP_OVERDUE, 31.0),
            (repo.FOLLOW_UP_LOW_SCORE, 3.0),
            (repo.FOLLOW_UP_LOW_YIELD, 41.0),
            (repo.FOLLOW_UP_OVERDUE, 98.0),
            (repo.FOLLOW_UP_LOW_SCORE, 1.0),
        ]
        ordered = sorted(items, key=lambda i: repo.follow_up_sort_key(*i))
        assert ordered == [
            (repo.FOLLOW_UP_LOW_SCORE, 1.0),
            (repo.FOLLOW_UP_LOW_SCORE, 3.0),
            (repo.FOLLOW_UP_OVERDUE, 98.0),
            (repo.FOLLOW_UP_OVERDUE, 31.0),
            (repo.FOLLOW_UP_LOW_YIELD, 41.0),
        ]


class TestLowYieldIsOnlyJudgedOnClosedCycles:
    def test_the_query_requires_a_harvested_cycle(self) -> None:
        # An inspection at ระยะงอก legitimately reports a low percentage of
        # plan. Flagging those would bury the harvests that genuinely came
        # up short, which is the entire point of the row.
        import inspect

        src = inspect.getsource(repo._flag_sets)
        assert "CYCLE_STATUS_HARVESTED" in src
        assert "final_yield_pct" in src

    def test_the_threshold_is_a_named_constant(self) -> None:
        assert repo.LOW_YIELD_PCT == Decimal("80")


class TestBreakdownGrouping:
    @pytest.mark.parametrize("group", repo.BREAKDOWN_GROUPS)
    def test_every_supported_grouping_survives(self, group) -> None:
        assert repo.normalize_group_by(group) == group

    @pytest.mark.parametrize("bad", [None, "", "province", "SUPPLIER"])
    def test_an_unknown_grouping_falls_back_to_supplier(self, bad) -> None:
        assert repo.normalize_group_by(bad) == repo.GROUP_SUPPLIER

    def test_the_range_filter_sits_in_the_join_not_the_where(self) -> None:
        # In the WHERE it would drop every plot with no inspections inside
        # the window — which are exactly the rows a comparison table exists
        # to surface.
        import inspect

        src = inspect.getsource(repo.get_executive_breakdown)
        assert "join_on.extend(window.conditions())" in src

    def test_grouping_is_done_on_the_plot_not_the_record(self) -> None:
        # What lets Supplier / crop / variety share one table: "แปลง" and
        # "ตรวจแล้ว" then mean the same thing in all three modes.
        import inspect

        src = inspect.getsource(repo.get_executive_breakdown)
        assert "Plot.current_crop" in src and "Plot.current_variety" in src
        assert ".select_from(Plot)" in src


class TestOverlapIsNotSummed:
    def test_the_schema_carries_a_distinct_total_beside_the_reasons(self) -> None:
        # One plot can be overdue AND low-scoring, so the three reason counts
        # do not add up to the total. Both must travel so the card can show
        # the total and list the reasons rather than sum them.
        from app.schemas.dashboard import ExecutiveSummary

        for field in ("actionable_plots", "actionable_overdue",
                      "actionable_low_score", "actionable_low_yield"):
            assert field in ExecutiveSummary.model_fields

    def test_the_total_is_computed_as_a_set_union(self) -> None:
        import inspect

        src = inspect.getsource(repo._actionable_counts)
        assert "union" in src and "len(union)" in src


class TestEndpoints:
    @pytest.mark.parametrize("path", [
        "/executive", "/executive/trend", "/executive/breakdown", "/executive/follow-up",
    ])
    def test_every_executive_endpoint_is_behind_the_same_key(self, path) -> None:
        assert _permission_keys(_route(path)) == {KEY}

    def test_the_scoped_summary_is_still_open(self) -> None:
        assert _permission_keys(_route("/summary")) == set()

    def test_the_follow_up_list_is_not_range_scoped(self) -> None:
        # A window would hide the plot nobody has touched in four months,
        # which is the one most worth finding.
        import inspect

        src = inspect.getsource(dashboard_api.get_executive_follow_up)
        assert "range" not in src.split('"""')[0]

    def test_the_summary_echoes_the_range_it_used(self) -> None:
        from app.schemas.dashboard import ExecutiveSummary

        assert "range" in ExecutiveSummary.model_fields
