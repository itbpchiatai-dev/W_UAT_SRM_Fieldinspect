"""Round 41 — the demo seeder's value IS its shape, so the shape is tested.

A mock seeder that produces uniform data is worse than no seeder: every
dashboard KPI reads 100%, the empty and bad cases never get designed, and
the gap only shows up on UAT. These tests pin the four decisions that keep
the dataset uneven, plus the localhost guard that keeps it off UAT.

Pure functions only — no DB. The insert path is exercised by running the
script against a local DB, which is what its own --clean/--reset flags and
closing report are for.
"""
from __future__ import annotations

import datetime
import random
from decimal import Decimal

import pytest

from app.db import seed_mock_dashboard_demo as demo


def _rng() -> random.Random:
    return random.Random(demo._RANDOM_SEED)


class TestLocalhostGuard:
    """The only thing standing between this script and UAT's real data."""

    @pytest.mark.asyncio
    async def test_refuses_a_non_local_host(self, monkeypatch) -> None:
        # A documentation-range address (RFC 5737), not a real host: the
        # repo is public and the guard works the same against any non-local
        # value, so naming an actual server here would be gratuitous.
        monkeypatch.setattr(
            demo, "get_settings",
            lambda: type("S", (), {"DB_HOST": "203.0.113.10", "DB_PORT": 5432,
                                   "DB_NAME": "srm_fieldinspect"})(),
        )
        with pytest.raises(SystemExit) as exc:
            await demo._require_localhost()
        assert "203.0.113.10" in str(exc.value)

    @pytest.mark.asyncio
    async def test_allows_localhost_and_loopback(self, monkeypatch) -> None:
        for host in ("localhost", "127.0.0.1"):
            monkeypatch.setattr(
                demo, "get_settings",
                lambda host=host: type("S", (), {"DB_HOST": host, "DB_PORT": 5432,
                                                 "DB_NAME": "srm_fieldinspect"})(),
            )
            await demo._require_localhost()  # must not raise


class TestVisitDates:
    def test_a_stale_plot_was_last_visited_more_than_the_threshold_ago(self) -> None:
        rng = _rng()
        for _ in range(200):
            dates = demo._visit_dates(rng, stale=True, count=3)
            assert dates, "a stale plot must still have visits"
            age = (demo._TODAY - dates[-1]).days
            assert age > demo._STALE_DAYS

    def test_a_current_plot_was_visited_within_the_threshold(self) -> None:
        rng = _rng()
        for _ in range(200):
            dates = demo._visit_dates(rng, stale=False, count=3)
            age = (demo._TODAY - dates[-1]).days
            assert age < demo._STALE_DAYS

    def test_nothing_falls_outside_the_demo_window(self) -> None:
        rng = _rng()
        oldest_allowed = demo._TODAY - datetime.timedelta(
            days=demo._SHAPE["months_back"] * 30
        )
        for _ in range(200):
            for d in demo._visit_dates(rng, stale=True, count=6):
                assert oldest_allowed <= d <= demo._TODAY

    def test_dates_come_back_oldest_first(self) -> None:
        # The record loop treats index 0 as the first visit of the season and
        # ripens the yield from there, so the order is load-bearing.
        rng = _rng()
        for _ in range(50):
            dates = demo._visit_dates(rng, stale=False, count=5)
            assert dates == sorted(dates)


class TestScores:
    def test_a_problem_plot_always_has_one_score_in_the_watch_band(self) -> None:
        rng = _rng()
        for _ in range(300):
            assert min(demo._scores(rng, problem=True)) <= 3

    def test_a_healthy_plot_never_lands_in_the_watch_band(self) -> None:
        # Otherwise "แปลงที่ควรเฝ้าระวัง" counts plots nobody marked as a
        # problem, and the share stops matching _SHAPE.
        rng = _rng()
        for _ in range(300):
            assert min(demo._scores(rng, problem=False)) > 3

    def test_scores_stay_inside_the_column_range(self) -> None:
        rng = _rng()
        for problem in (True, False):
            for _ in range(300):
                for value in demo._scores(rng, problem):
                    assert 1 <= value <= 10


class TestSupplierFactors:
    def test_the_spread_covers_the_configured_range(self) -> None:
        ids = [f"sup-{i:02d}" for i in range(20)]
        factors = demo._supplier_factors(ids, _rng())
        assert len(factors) == 20
        assert min(factors.values()) < Decimal("0.75")
        assert max(factors.values()) > Decimal("0.95")

    def test_the_result_does_not_depend_on_input_order(self) -> None:
        # Dict/query order must not decide which supplier looks good.
        ids = [f"sup-{i:02d}" for i in range(10)]
        a = demo._supplier_factors(ids, _rng())
        b = demo._supplier_factors(list(reversed(ids)), _rng())
        assert a == b

    def test_degenerate_inputs(self) -> None:
        assert demo._supplier_factors([], _rng()) == {}
        single = demo._supplier_factors(["only"], _rng())
        assert list(single) == ["only"]

    def test_no_supplier_is_pushed_below_a_sane_floor(self) -> None:
        ids = [f"sup-{i:02d}" for i in range(40)]
        for value in demo._supplier_factors(ids, _rng()).values():
            assert value >= Decimal("0.40")


class TestStageProgression:
    def test_only_a_harvested_plot_reaches_the_harvest_stage(self) -> None:
        rng = _rng()
        for _ in range(200):
            for i in range(4):
                stage = demo._stage_for(i, 4, harvested=False, rng=rng)
                assert stage != demo._STAGE_HARVEST

    def test_a_harvested_plot_harvests_on_its_last_visit_only(self) -> None:
        rng = _rng()
        for _ in range(200):
            stages = [demo._stage_for(i, 4, harvested=True, rng=rng) for i in range(4)]
            assert stages[-1] == demo._STAGE_HARVEST
            assert demo._STAGE_HARVEST not in stages[:-1]

    def test_a_single_visit_plot_is_handled(self) -> None:
        rng = _rng()
        assert demo._stage_for(0, 1, harvested=True, rng=rng) == demo._STAGE_HARVEST
        assert demo._stage_for(0, 1, harvested=False, rng=rng) != demo._STAGE_HARVEST


class TestDeterminism:
    def test_the_same_seed_produces_the_same_dataset(self) -> None:
        # A number in a screenshot has to be reproducible.
        def run() -> list:
            rng = _rng()
            return [
                demo._visit_dates(rng, stale=rng.random() < 0.2, count=3)
                for _ in range(50)
            ]

        assert run() == run()


class TestShapeConstants:
    def test_every_share_is_a_minority(self) -> None:
        # The whole point is an uneven dataset; a share at 1.0 would peg the
        # matching KPI exactly the way seed_mock_records_extra does.
        for key in ("plot_coverage", "stale_share", "cleaned_share_of_harvest",
                    "problem_plot_share", "notes_share", "recommendation_share"):
            assert 0 < demo._SHAPE[key] < 1, key

    def test_the_tag_is_what_clean_deletes_by(self) -> None:
        # --clean issues a DELETE with this predicate and nothing else, so it
        # has to name both the key and the value. Rendered with literal binds
        # because str() leaves both as placeholders and would pass on a
        # predicate that matched every row.
        from sqlalchemy.dialects import postgresql

        assert demo.SEED_TAG == "dashboard-demo"
        sql = str(demo._tag_filter().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        ))
        assert "custom_fields ->> 'mockSeed'" in sql
        assert "'dashboard-demo'" in sql
