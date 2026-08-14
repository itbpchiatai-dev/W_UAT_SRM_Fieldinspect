"""CT App Registry integration — telemetry push client.

ดู docs/ops/registry.md §4-§5. โมดูล drop-in — ปกติไม่ต้องแก้
collect_yesterday_metrics() ดึงเมตริก default จาก ai_call_logs /
activity_logs / system_logs; เพิ่ม domain metric ของ app ลงใน
extraMetrics ได้ตามต้องการ
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import httpx
import structlog
from sqlalchemy import distinct, func, select

from app.core.config import get_settings
from app.db.models.activity_log import ActivityLog
from app.db.models.ai_call_log import AiCallLog
from app.db.models.system_log import SystemLog
from app.db.session import get_db_session

logger = structlog.get_logger(__name__)


async def push_daily_telemetry(metrics: dict) -> bool:
    """ส่ง telemetry รายวันเข้า CT App Registry (เรียกจาก scheduler job).

    metrics: dict camelCase ตาม docs/ops/registry.md §4.2 — ต้องมี
    key `date` (YYYY-MM-DD); field อื่น optional. idempotent ตาม date —
    ส่งซ้ำวันเดิม = แทนที่ของเดิม จึง retry ได้ปลอดภัย.

    คืน True ถ้าสำเร็จ — ไม่ raise ออกไปทำ caller ล่ม (telemetry เป็น
    งาน background, ห้ามทำ app หลักล่ม — §6).
    """
    settings = get_settings()
    if not settings.REGISTRY_URL or not settings.REGISTRY_API_KEY:
        logger.info("registry.telemetry.skipped", reason="not onboarded")
        return False

    url = (
        f"{settings.REGISTRY_URL.rstrip('/')}"
        f"/api/v1/projects/{settings.PROJECT_SLUG}/telemetry"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url, json=metrics,
                headers={"X-API-Key": settings.REGISTRY_API_KEY},
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        # 403 = ยังไม่ approve / key ผิด — retry รอบถัดไป (idempotent)
        logger.warning(
            "registry.telemetry.rejected",
            status=e.response.status_code, body=e.response.text[:500],
        )
        return False
    except httpx.HTTPError as e:
        logger.warning("registry.telemetry.error", error=str(e))
        return False

    logger.info("registry.telemetry.pushed", date=metrics.get("date"))
    return True


async def collect_yesterday_metrics() -> dict:
    """รวมเมตริกของ "เมื่อวาน" สำหรับ push เข้า registry.

    Fields ตาม docs/ops/registry.md §4.2:
    aiCalls / aiInputTokens / aiOutputTokens / aiCostUsd → ai_call_logs
    activeUsers / totalLogins / failedLogins                → activity_logs
    errorCount                                              → system_logs

    p95LatencyMs ขึ้นกับ middleware ของแต่ละ app — ปล่อย null โดย default
    extraMetrics: เพิ่ม domain metric เฉพาะ app ที่ field ข้างบนไม่ครอบ

    DB session เปิดเองภายใน — caller ไม่ต้องส่ง db เพราะ job รันจาก
    scheduler ที่ไม่มี request scope (ดู core/scheduler.py).
    """
    yesterday = date.today() - timedelta(days=1)
    start = datetime.combine(yesterday, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    async with get_db_session() as db:
        ai_row = (await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(AiCallLog.input_tokens), 0),
                func.coalesce(func.sum(AiCallLog.output_tokens), 0),
                func.coalesce(func.sum(AiCallLog.cost_usd), 0),
            ).where(
                AiCallLog.created_at >= start,
                AiCallLog.created_at < end,
            )
        )).one()

        active_users = (await db.execute(
            select(func.count(distinct(ActivityLog.user_id)))
            .where(
                ActivityLog.created_at >= start,
                ActivityLog.created_at < end,
                ActivityLog.user_id.is_not(None),
            )
        )).scalar() or 0

        total_logins = (await db.execute(
            select(func.count())
            .select_from(ActivityLog)
            .where(
                ActivityLog.created_at >= start,
                ActivityLog.created_at < end,
                ActivityLog.action_type == "login",
            )
        )).scalar() or 0

        failed_logins = (await db.execute(
            select(func.count())
            .select_from(ActivityLog)
            .where(
                ActivityLog.created_at >= start,
                ActivityLog.created_at < end,
                ActivityLog.action_type == "login_failed",
            )
        )).scalar() or 0

        error_count = (await db.execute(
            select(func.count())
            .select_from(SystemLog)
            .where(
                SystemLog.created_at >= start,
                SystemLog.created_at < end,
                SystemLog.status == "failure",
            )
        )).scalar() or 0

    return {
        "date": yesterday.isoformat(),
        "aiCalls": int(ai_row[0] or 0),
        "aiInputTokens": int(ai_row[1] or 0),
        "aiOutputTokens": int(ai_row[2] or 0),
        # registry expects decimal-as-string ≤4dp (§4.2)
        "aiCostUsd": f"{float(ai_row[3] or 0):.4f}",
        "activeUsers": int(active_users),
        "totalLogins": int(total_logins),
        "failedLogins": int(failed_logins),
        "errorCount": int(error_count),
    }
