"""AppSettingService — Pattern C admin-config reader.

Reads JSONB values from `app_settings`. Returns the raw value (bool /
int / str / dict / list) or `default` when the key is missing.

Defaults are seeded by `app.seed._seed_app_settings`. Admin writes go
through `app/api/v1/admin_settings.py` (super-admin only).

See docs/admin-config.md.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.app_setting import AppSetting


class AppSettingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, key: str, default: Any = None) -> Any:
        result = await self.db.execute(
            select(AppSetting.value).where(AppSetting.key == key)
        )
        row = result.scalar_one_or_none()
        return row if row is not None else default
