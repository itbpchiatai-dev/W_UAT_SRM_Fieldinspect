"""Scheduler jobs after round H.

Three jobs a day failed for a week on UAT and produced not one line anywhere.
The reason was structural, not a typo: _audit_job records each run's outcome
in system_logs, system_logs is one of the partitioned tables that had run out,
so the job failed, the attempt to RECORD that failure also failed, and the
logging exception replaced the real one on the way out of the `finally`.

A reporting path must never depend on the thing it reports about. These tests
pin that, and the two other properties that came out of the same incident:
the partition job tops up far enough ahead to survive a long outage, and it
says how much runway is left instead of only that it ran.

DB-free — the session and the logger are faked.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.core import scheduler as sched


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()


def _patch_session():
    return patch.object(sched, "get_db_session", _fake_session)


# ------------------------------------------------------- the silent failure --

async def test_a_logging_failure_never_replaces_the_real_error():
    """THE bug. The job's own exception must survive a broken logger — it is
    the only thing that says what actually went wrong."""
    with _patch_session(), patch.object(sched, "SystemLogger") as logger_cls:
        logger_cls.return_value.log_job = AsyncMock(
            side_effect=RuntimeError("no partition of relation system_logs")
        )

        with pytest.raises(ValueError, match="the real failure"):
            async with sched._audit_job("log_partitions"):
                raise ValueError("the real failure")


async def test_a_logging_failure_does_not_fail_a_job_that_worked():
    """The inverse: the job did its work. Losing the audit line must not
    turn that into a failure the operator has to chase."""
    with _patch_session(), patch.object(sched, "SystemLogger") as logger_cls:
        logger_cls.return_value.log_job = AsyncMock(side_effect=RuntimeError("db down"))

        async with sched._audit_job("log_retention"):
            pass  # no exception expected out of the context manager


async def test_a_logging_failure_is_still_reported_to_stdout():
    """Swallowed, but never silent — structlog goes to the container log,
    which is the one channel that does not depend on the database."""
    with _patch_session(), patch.object(sched, "SystemLogger") as logger_cls, \
         patch.object(sched.logger, "error") as log_error:
        logger_cls.return_value.log_job = AsyncMock(side_effect=RuntimeError("db down"))

        async with sched._audit_job("log_partitions"):
            pass

    log_error.assert_called_once()
    assert log_error.call_args.args[0] == "scheduler.job_log_failed"


async def test_a_successful_run_is_recorded_as_success():
    with _patch_session(), patch.object(sched, "SystemLogger") as logger_cls:
        log_job = AsyncMock()
        logger_cls.return_value.log_job = log_job

        async with sched._audit_job("log_retention"):
            pass

    assert log_job.call_args.kwargs["status"] == "success"


async def test_a_failed_run_is_recorded_as_failure_and_still_raises():
    with _patch_session(), patch.object(sched, "SystemLogger") as logger_cls:
        log_job = AsyncMock()
        logger_cls.return_value.log_job = log_job

        with pytest.raises(ValueError):
            async with sched._audit_job("log_partitions"):
                raise ValueError("boom")

    assert log_job.call_args.kwargs["status"] == "failure"


# ------------------------------------------------------- the partition job --

async def test_partition_job_tops_up_years_ahead():
    """Two months of headroom is two months from a broken job to an outage."""
    with _patch_session(), \
         patch.object(sched, "_audit_job", lambda name: _fake_session()), \
         patch.object(sched, "ensure_partitions_exist", AsyncMock(return_value=0)) as ensure, \
         patch.object(sched, "months_of_coverage", AsyncMock(return_value=24)):
        await sched._ensure_log_partitions()

    assert ensure.call_args.kwargs["months_ahead"] >= 12


async def test_partition_job_shouts_when_the_runway_is_short():
    """This is the state that preceded the incident; it has to be loud."""
    low = sched.PARTITION_COVERAGE_WARN_MONTHS - 1
    with _patch_session(), \
         patch.object(sched, "_audit_job", lambda name: _fake_session()), \
         patch.object(sched, "ensure_partitions_exist", AsyncMock(return_value=0)), \
         patch.object(sched, "months_of_coverage", AsyncMock(return_value=low)), \
         patch.object(sched.logger, "error") as log_error:
        await sched._ensure_log_partitions()

    log_error.assert_called_once()
    assert log_error.call_args.args[0] == "scheduler.partitions.coverage_low"
    assert log_error.call_args.kwargs["months_remaining"] == low


async def test_partition_job_reports_runway_on_a_healthy_run_too():
    with _patch_session(), \
         patch.object(sched, "_audit_job", lambda name: _fake_session()), \
         patch.object(sched, "ensure_partitions_exist", AsyncMock(return_value=3)), \
         patch.object(sched, "months_of_coverage", AsyncMock(return_value=24)), \
         patch.object(sched.logger, "info") as log_info:
        await sched._ensure_log_partitions()

    assert log_info.call_args.kwargs["months_remaining"] == 24
    assert log_info.call_args.kwargs["created"] == 3


# ------------------------------------------------------------- the cadence --

def test_partition_job_runs_daily_not_monthly():
    """It used to run on the 25th, so a failed run had a month to become an
    outage. Creating partitions is idempotent and cheap; run it daily."""
    with patch.object(sched, "AsyncIOScheduler") as scheduler_cls:
        sched._scheduler = None
        sched.start_scheduler()

    jobs = {
        call.kwargs["id"]: call.args[1]
        for call in scheduler_cls.return_value.add_job.call_args_list
    }
    sched._scheduler = None

    trigger = jobs["log_partitions"]
    assert isinstance(trigger, CronTrigger)
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["day"] == "*", "a monthly cadence is what let the outage happen"
    assert fields["hour"] == "2"
