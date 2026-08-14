"""APScheduler — background jobs.

Wires three recurring jobs:
- registry_telemetry  — daily push to CT App Registry (ดู docs/ops/registry.md §5.2)
- log_partitions      — monthly: create next 2 months of log partitions
- log_retention       — daily: drop partitions older than retention_days

start/stop จาก main.py lifespan.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import get_db_session
from app.integrations.registry import collect_yesterday_metrics, push_daily_telemetry
from app.services.loggers.partition_manager import ensure_partitions_exist
from app.services.loggers.retention import drop_old_partitions
from app.services.loggers.system_logger import SystemLogger

logger = structlog.get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def _audit_job(job_name: str) -> AsyncIterator[None]:
    """Wrap a job body so every run lands in system_logs as either
    success or failure, with duration_ms and the masked error message.

    Without this the admin System Logs page is empty even when the
    scheduler is firing 3 jobs a day — structlog goes to stdout, not DB,
    so an operator can't see "was last night's retention run OK?"
    from the admin UI.
    """
    start = time.monotonic()
    caught: Exception | None = None
    try:
        yield
    except Exception as e:
        caught = e
        raise
    finally:
        async with get_db_session() as db:
            await SystemLogger(db).log_job(
                job_name=job_name,
                status="failure" if caught else "success",
                duration_ms=int((time.monotonic() - start) * 1000),
                error=caught,
            )
            await db.commit()


async def _push_yesterday_telemetry() -> None:
    """job: รวมเมตริกเมื่อวาน แล้ว push เข้า CT App Registry."""
    async with _audit_job("registry_telemetry"):
        metrics = await collect_yesterday_metrics()
        await push_daily_telemetry(metrics)


async def _ensure_log_partitions() -> None:
    """job: create monthly partitions for log tables (idempotent)."""
    async with _audit_job("log_partitions"):
        async with get_db_session() as db:
            await ensure_partitions_exist(db, months_ahead=2)
        logger.info("scheduler.partitions.ensured")


async def _run_log_retention() -> None:
    """job: drop partitions older than retention_days."""
    async with _audit_job("log_retention"):
        async with get_db_session() as db:
            dropped = await drop_old_partitions(db)
        logger.info("scheduler.retention.complete", dropped=dropped)


def start_scheduler() -> None:
    """เริ่ม scheduler + ลงทะเบียน job — เรียกจาก FastAPI lifespan (startup)."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _push_yesterday_telemetry,
        CronTrigger(hour=1, minute=0),  # ตี 1 ของทุกวัน
        id="registry_telemetry",
        replace_existing=True,
    )
    _scheduler.add_job(
        _ensure_log_partitions,
        CronTrigger(day=25, hour=2, minute=0),  # วันที่ 25 ของทุกเดือน
        id="log_partitions",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_log_retention,
        CronTrigger(hour=3, minute=0),  # ตี 3 ของทุกวัน
        id="log_retention",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "scheduler.started",
        jobs=["registry_telemetry", "log_partitions", "log_retention"],
    )


def stop_scheduler() -> None:
    """หยุด scheduler — เรียกจาก FastAPI lifespan (shutdown)."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler.stopped")
